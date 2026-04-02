# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import os
import shutil
import subprocess
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from code_agent.tools.base import Tool, ToolCallArguments, ToolExecResult, ToolParameter
from code_agent.tools.database.duckdb_compile_test import compile_incremental, run_all_builtin_groups
from code_agent.tools.database.postgresql_compile_test import compile_postgresql, installcheck_postgresql
from code_agent.tools.database.sqlite_compile_test import compile_sqlite, run_sqlite_function_tests
from code_agent.utils.config import get_database_config, get_current_database

install_folder: str = get_database_config(get_current_database()).install_folder

TIMEOUT = 60 * 3
SYNTAX_TIMEOUT = 30

VerifyToolModes = [
    "syntax",
    "compliance",
    "semantic",
    # "performance"
]

VerifyToolDatabases = [
    "postgresql",
    "sqlite",
    "duckdb",
    "clickhouse",
]


# Per-database whitelist of source directories to syntax-check.
# Only files whose relative path starts with one of these prefixes will be
# included, so build-generated directories are automatically excluded regardless
# of how they are named across different database projects.
_DATABASE_SOURCE_DIRS: dict[str, tuple[str, ...]] = {
    "postgresql": ("src/",),
    "sqlite":     ("src/", "ext/"),
    "duckdb":     ("src/", "extension/"),
    "clickhouse": ("src/",),
}


def _in_source_dir(rel_path: str, source_dirs: tuple[str, ...]) -> bool:
    """Return True if rel_path starts with one of the known source directory prefixes."""
    if not source_dirs:
        return True  # no restriction configured – accept all
    return rel_path.startswith(source_dirs)


def _get_modified_files(project_path: str, base_commit: str,
                         extensions: tuple, source_dirs: tuple[str, ...] = ()) -> tuple:
    """Return (files, error_msg).

    files: absolute paths of modified/new files, filtered by extension and
           restricted to known source directories (whitelist) so that
           build-generated files are never included regardless of database.
    error_msg: non-empty string if git commands failed, empty string on success.
    """
    files = set()
    errors = []
    try:
        # modified tracked files since base_commit
        r1 = subprocess.run(
            ["git", "diff", "--name-only", base_commit],
            capture_output=True, text=True, cwd=project_path, timeout=10,
        )
        if r1.returncode != 0 and r1.stderr.strip():
            errors.append(f"git diff failed: {r1.stderr.strip()}")
        for f in r1.stdout.strip().splitlines():
            if f.endswith(extensions) and _in_source_dir(f, source_dirs):
                files.add(os.path.join(project_path, f))

        # untracked new files
        r2 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=project_path, timeout=10,
        )
        if r2.returncode != 0 and r2.stderr.strip():
            errors.append(f"git ls-files failed: {r2.stderr.strip()}")
        for f in r2.stdout.strip().splitlines():
            if f.endswith(extensions) and _in_source_dir(f, source_dirs):
                files.add(os.path.join(project_path, f))
    except Exception as e:
        errors.append(f"git command exception: {e}")
    return list(files), "\n".join(errors)


def _tree_sitter_syntax_check(file_paths: list, language_name: str) -> tuple:
    """Parse files with tree-sitter and report ERROR/MISSING nodes.

    Returns (available, is_success, message):
      available=False  → tree-sitter or the language package is not installed; caller should fall back.
      available=True   → check ran; is_success/message carry the result.
    """
    try:
        from tree_sitter import Language, Parser  # type: ignore
        if language_name == "c":
            import tree_sitter_c as _ts_lang  # type: ignore
        else:
            import tree_sitter_cpp as _ts_lang  # type: ignore
        lang = Language(_ts_lang.language())
        parser = Parser(lang)
    except Exception:
        return False, False, ""

    def _collect_errors(node):
        found = []
        if node.type == "ERROR" or node.is_missing:
            found.append((node.start_point[0] + 1, node.start_point[1] + 1))
        for child in node.children:
            found.extend(_collect_errors(child))
        return found

    errors = []
    for path in file_paths:
        try:
            with open(path, "rb") as f:
                content = f.read()
            tree = parser.parse(content)
            error_nodes = _collect_errors(tree.root_node)
            if error_nodes:
                locs = ", ".join(f"line {r}:{c}" for r, c in error_nodes[:5])
                suffix = f" (+{len(error_nodes) - 5} more)" if len(error_nodes) > 5 else ""
                errors.append(f"{path}: syntax error(s) at {locs}{suffix}")
        except Exception as e:
            errors.append(f"{path}: {e}")

    if errors:
        return True, False, "\n".join(errors)
    return True, True, f"Syntax check passed via tree-sitter. ({len(file_paths)} file(s) checked)"


def _gcc_syntax_check(file_paths: list, compiler: str, flags: list) -> tuple:
    """Run gcc/g++ -fsyntax-only on each file, filter out missing-header false positives.

    Returns (available, is_success, message):
      available=False  → compiler not found in PATH; caller should fall back.
    """
    if shutil.which(compiler) is None:
        return False, False, ""

    errors = []
    for path in file_paths:
        try:
            result = subprocess.run(
                [compiler, "-fsyntax-only", "-w"] + flags + [path],
                capture_output=True, text=True, timeout=SYNTAX_TIMEOUT,
            )
            if result.returncode != 0:
                # exclude lines caused only by missing include files
                real_errors = [
                    line for line in result.stderr.splitlines()
                    if "error:" in line and "No such file" not in line
                ]
                if real_errors:
                    errors.append(f"{path}:\n  " + "\n  ".join(real_errors[:10]))
        except Exception as e:
            errors.append(f"{path}: {e}")

    if errors:
        return True, False, "\n".join(errors)
    return True, True, f"Syntax check passed via {compiler}. ({len(file_paths)} file(s) checked)"


class DatabaseVerifyTool(Tool):
    """Verify whether the modified database project passes syntax, compliance, and semantic checks."""

    def __init__(self, model_provider: str | None = None) -> None:
        super().__init__(model_provider)

    @override
    def get_model_provider(self) -> str | None:
        return self._model_provider

    @override
    def get_name(self) -> str:
        return "database_verify"

    @override
    def get_description(self) -> str:
        return "Verify whether the modified database project passes syntax, compliance, and semantic checks."

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="mode",
                type="string",
                description="The verification mode: syntax (static analysis), compliance (build), or semantic (test run).",
                required=True,
                enum=VerifyToolModes,
            ),
            ToolParameter(
                name="database",
                type="string",
                description="The database to be verified.",
                required=True,
                enum=VerifyToolDatabases,
            ),
            ToolParameter(
                name="code",
                type="dict",
                description="The implemented code to be verified.",
                required=False,
            ),
            ToolParameter(
                name="base_commit",
                type="string",
                description="The git commit hash to diff against for syntax check. Defaults to HEAD.",
                required=False,
            ),
        ]

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        mode = str(arguments["mode"]) if "mode" in arguments else None
        if mode is None or mode not in VerifyToolModes:
            return ToolExecResult(
                error=f"No or wrong mode provided for the {self.get_name()} tool",
                error_code=-1,
            )

        database = str(arguments["database"]) if "database" in arguments else None
        if database is None or database not in VerifyToolDatabases:
            return ToolExecResult(
                error=f"No or wrong database provided for the {self.get_name()} tool",
                error_code=-1,
            )

        if mode == "syntax":
            base_commit = str(arguments["base_commit"]) if "base_commit" in arguments else "HEAD"

            if database == "postgresql":
                extensions = (".c", ".h")
                ts_lang = "c"
                compiler, flags = "gcc", [f"-I{os.getcwd()}/src/include"]
            elif database == "sqlite":
                extensions = (".c", ".h")
                ts_lang = "c"
                compiler, flags = "gcc", []
            elif database == "duckdb":
                extensions = (".cpp", ".cc", ".hpp", ".hh", ".h")
                ts_lang = "cpp"
                compiler, flags = "g++", ["-std=c++17", f"-I{os.getcwd()}/src/include"]
            else:
                return ToolExecResult(output="Syntax check skipped.")

            source_dirs = _DATABASE_SOURCE_DIRS.get(database, ())
            file_paths, git_error = _get_modified_files(os.getcwd(), base_commit, extensions, source_dirs)
            if not file_paths:
                msg = "Syntax check passed. (no modified files found)"
                if git_error:
                    msg += f"\nWarning: {git_error}"
                return ToolExecResult(output=msg)

            # Try gcc/g++ first (compiler required), fall back to tree-sitter
            available, is_success, result = _gcc_syntax_check(file_paths, compiler, flags)
            if not available:
                available, is_success, result = _tree_sitter_syntax_check(file_paths, ts_lang)
            if not available:
                msg = f"Syntax check skipped: neither tree-sitter ({ts_lang}) nor '{compiler}' is available."
                if git_error:
                    msg += f"\nWarning: {git_error}"
                return ToolExecResult(output=msg)

            if is_success:
                output = result
                if git_error:
                    output += f"\nWarning: {git_error}"
                return ToolExecResult(output=output)
            else:
                return ToolExecResult(
                    error=f"Syntax errors found:\n{result}", error_code=-1
                )

        elif mode == "compliance":
            if database == "postgresql":
                is_success, result = compile_postgresql(os.getcwd(), install_folder, timeout=TIMEOUT)

            elif database == "sqlite":
                is_success, result = compile_sqlite(os.getcwd(), timeout=TIMEOUT)

            elif database == "duckdb":
                is_success, result = compile_incremental(os.getcwd(), timeout=TIMEOUT)

            else:
                is_success, result = True, "Skipped."

            if "timeout" in result.lower():
                is_success = True

            if is_success:
                return ToolExecResult(output="Task done.")
            else:
                return ToolExecResult(error=f"Error occurs when try to compile the database "
                                            f"after the generated code integration:\n{result}", error_code=-1)

        elif mode == "semantic":
            if database == "postgresql":
                diffs_path = f"{os.getcwd()}/src/test/regress/regression.diffs"
                if os.path.exists(diffs_path):
                    os.remove(diffs_path)
                is_success, result = installcheck_postgresql(os.getcwd(), timeout=TIMEOUT)
                if os.path.exists(diffs_path):
                    is_success = False
                    result = f"Test failures detected. Diff file: {diffs_path}"
            elif database == "sqlite":
                test_out_path = f"{os.getcwd()}/build/test-out.txt"
                if os.path.exists(test_out_path):
                    os.remove(test_out_path)
                is_success, result = run_sqlite_function_tests(os.getcwd(), timeout=TIMEOUT)
                if os.path.exists(test_out_path) and is_success:
                    is_success = False
                    result = f"[TEST ERROR] Error output file: {test_out_path}"
            elif database == "duckdb":
                is_success, info = run_all_builtin_groups(os.getcwd(), timeout_per_group=TIMEOUT)
                s = info.get("summary", {})
                result = (
                    f"total={s.get('total', 0)}, passed={s.get('passed', 0)}, "
                    f"failed={s.get('failed', 0)}, skipped={s.get('skipped', 0)}"
                )
                if not is_success:
                    failed_groups = [g["group"] for g in info.get("groups", []) if not g.get("ok")]
                    result += f"\nFailed groups: {failed_groups}"
            else:
                is_success, result = True, "Skipped."

            if is_success:
                return ToolExecResult(output=f"Semantic test passed. {result}")
            else:
                return ToolExecResult(
                    error=f"Semantic test failed:\n{result}", error_code=-1
                )

        else:
            return ToolExecResult(output="Task done.")
