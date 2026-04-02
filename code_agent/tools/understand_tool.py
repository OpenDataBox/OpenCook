# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import json
import math
import os
import re
import subprocess
try:
    from typing import override
except ImportError:
    def override(func):
        return func

import pandas as pd
import understand

from code_agent.tools.base import Tool, ToolCallArguments, ToolExecResult, ToolParameter

_COMMANDS = [
    "get_directory_structure",
    "get_function_content",
    "get_function_declaration",
    "get_function_dependencies",
    "get_file_dependencies",
    "get_data_control_content",
    "find_func_by_re",
]


class UnderstandTool(Tool):
    """Tool for static code analysis using SciTools Understand, providing dependency info for the database codebase."""

    def __init__(self, model_provider: str | None = None, language: str = None, project_root: str = None) -> None:
        self.language = language
        self.project_root = project_root
        self.project_name = os.path.basename(self.project_root)
        self.udb_path = os.path.join(os.path.dirname(self.project_root), f"{self.project_name}.und")

        if not os.path.exists(self.udb_path):
            self.create_udb()
        self.db = understand.open(self.udb_path)

        super().__init__(model_provider)

    def create_udb(self):
        try:
            subprocess.check_output(
                "und create -db {udb_path} -languages {lang}".format(udb_path=self.udb_path,
                                                                     lang=self.language),
                shell=True)
            subprocess.check_output("und add -db {udb_path} {project} analyze -all".format(
                udb_path=self.udb_path, project=self.project_root), shell=True)
        except subprocess.CalledProcessError:
            import traceback
            traceback.print_exc()
            raise Exception("Failed to create udb file.")

        print("udb created")

    # ------------------------------------------------------------------ #
    #  Directory / file structure                                          #
    # ------------------------------------------------------------------ #

    def get_directory_structure(self):
        """Return a nested dict representing the project directory tree."""
        def traverse_directory(dir_path):
            items = os.listdir(dir_path)
            folder_contents = {}
            for item in items:
                item_path = os.path.join(dir_path, item)
                if os.path.isfile(item_path):
                    folder_contents[item] = None
                elif os.path.isdir(item_path):
                    folder_contents[item] = traverse_directory(item_path)
            return folder_contents

        return traverse_directory(self.project_root)

    # ------------------------------------------------------------------ #
    #  Function content retrieval                                          #
    # ------------------------------------------------------------------ #

    def get_function_content(self, files: list[str], functions: list[str]) -> dict:
        """Return source contents of the given (file, function) pairs.

        Args:
            files: list of absolute file paths.
            functions: list of function names, parallel to *files*.

        Returns:
            dict mapping function_name -> source content string.
        """
        combine: dict[str, set] = {}
        for file, function in zip(files, functions):
            combine.setdefault(file, set()).add(function)

        contents = {}
        for file_ent in self.db.ents("File"):
            file_name = file_ent.longname()
            if file_name not in combine:
                continue
            for function in file_ent.ents("Define", "Function"):
                if function.name() in combine[file_name]:
                    contents[function.name()] = function.contents()

        return contents

    # ------------------------------------------------------------------ #
    #  SQLite built-in function registration lookup                        #
    # ------------------------------------------------------------------ #

    def find_func_by_re(self, key_word: str) -> list[str]:
        """Find the C implementation function name for a SQLite built-in *key_word*.

        Searches the standard SQLite function registration files (func.c, date.c,
        json.c, window.c, alter.c) for FUNCTION / MFUNCTION / JFUNCTION macros.

        Returns:
            list of implementation function name strings.
        """
        func_loads = [
            f"{self.project_root}/src/func.c",
            f"{self.project_root}/src/date.c",
            f"{self.project_root}/src/json.c",
            f"{self.project_root}/src/window.c",
            f"{self.project_root}/src/alter.c",
        ]

        content = ""
        for func_load in func_loads:
            try:
                with open(func_load, "r") as rf:
                    content += rf.read() + "\n"
            except FileNotFoundError:
                continue

        pattern = rf'([A-Za-z_][A-Za-z0-9_]*)\(\s*({key_word}\s*,[\s\S]*?)\s*\)\s*,'
        matches = re.findall(pattern, content, re.DOTALL)

        key_functions = []
        for typ, match in matches:
            item = match.split(",")

            if typ == "MFUNCTION":
                if len(item) != 4:
                    continue
                key_functions.append(item[-1].strip())

            elif typ == "JFUNCTION":
                if len(item) != 8:
                    continue
                key_functions.append(item[-1].strip())

            else:
                if len(item) < 5:
                    continue
                is_numeric = all(item[i].replace("-", "").strip().isdigit() for i in range(1, 4))
                if not is_numeric:
                    continue
                for i in range(4, len(item)):
                    if item[i].strip() and item[i].strip()[0].islower():
                        key_functions.append(item[i].strip())

        return key_functions

    # ------------------------------------------------------------------ #
    #  Function list (used to build the DataFrame for get_relate_files)   #
    # ------------------------------------------------------------------ #

    def get_function(self) -> pd.DataFrame:
        """Return a DataFrame of all functions defined within the project.

        Scans every file under ``self.project_root`` and collects
        (file_name, function_name) pairs.

        Returns:
            DataFrame with columns ``file_name`` and ``function_name``.
        """
        data = []
        for file in self.db.ents("File"):
            if not file.longname().startswith(self.project_root):
                continue
            for function in file.ents("Define", "Function"):
                data.append((file.longname(), function.name()))
        return pd.DataFrame(data, columns=["file_name", "function_name"])

    # ------------------------------------------------------------------ #
    #  Related file lookup (offline workflow — needs pre-generated data)   #
    # ------------------------------------------------------------------ #

    def get_relate_files(self, key_functions: list[str], all_func_list: pd.DataFrame):
        """Look up the source file for each function in *key_functions*.

        Args:
            key_functions: list of function names to look up.
            all_func_list: DataFrame with columns ``file_name`` and ``function_name``
                           (typically generated by an offline ``get_function()`` scan).

        Returns:
            (files, key_functions_filtered) — parallel lists with functions that
            were successfully resolved.
        """
        files = []
        key_functions_filtered = []
        for key_function in key_functions:
            result = all_func_list.loc[all_func_list["function_name"] == key_function, "file_name"]
            if len(result) != 0:
                files.append(result.iloc[0])
                key_functions_filtered.append(key_function)
            else:
                print(f"Do not find the related file of `{key_function}`.")
        return files, key_functions_filtered

    # ------------------------------------------------------------------ #
    #  Data/control flow — reads from pre-generated Excel (offline)        #
    # ------------------------------------------------------------------ #

    def get_data_control_flow(self, analyze_file: str, target_lines: list[int]) -> list[int]:
        """Return define-site line numbers for variables used at *target_lines*.

        Requires pre-generated parameter Excel files from ``parameter_reference()``.

        Args:
            analyze_file: absolute path of the source file being analysed.
            target_lines: line numbers of interest.

        Returns:
            list of line numbers where the relevant variables are defined.

        Raises:
            FileNotFoundError: if the pre-generated Excel file is missing.
        """
        parameter_dir = os.path.join(os.path.dirname(self.project_root), self.project_name, "parameters")
        file_dir = analyze_file.replace("\\", "_").replace("/", "_").replace(":", "_").replace(".", "")
        full_dir = os.path.join(parameter_dir, f"{file_dir}_parameter.xlsx")

        df = pd.read_excel(full_dir)

        result_set: set[int] = set()
        for target_line in target_lines:
            parameter = df[df["行号"] == target_line]
            if parameter.empty:
                continue
            parameter_name = parameter["参数名"].iloc[0]
            upper_rows = df[df["行号"] <= target_line]
            parameters = upper_rows[upper_rows["参数名"] == parameter_name]
            parameters_define = parameters[parameters["参数调用类型"] == "Define"]
            result_set.update(parameters_define["行号"].tolist())

        return list(result_set)

    # ------------------------------------------------------------------ #
    #  Dependency helpers                                                  #
    # ------------------------------------------------------------------ #

    def prune_function_implementation(self, code):
        pattern1 = r'(\w+::)?\w+\([^;{]*\)\s*(?::[^{]*)?\s*\{[^}]*\}'
        pattern2 = r'(\w+(?:<[^>]*>)?\s+)?(\w+::)?\w+\([^;{]*\)\s*(?:const\s*)?\{[^}]*\}'
        pattern3 = r'(\w+(?:::\w+)?\s+)+(\w+::)?\w+\([^;{]*\)\s*(?:const\s*)?\{[^}]*\}'

        def extract_declaration(function_impl):
            brace_pos = function_impl.find('{')
            if brace_pos != -1:
                declaration = function_impl[:brace_pos].strip()
                if not declaration.endswith(';'):
                    declaration += ';'
                return declaration
            return function_impl

        cleaned_code = code
        for pattern in [pattern1, pattern2, pattern3]:
            cleaned_code = re.sub(pattern, lambda m: extract_declaration(m.group()), cleaned_code, flags=re.DOTALL)

        return cleaned_code

    def format_dependency_info(self, entity, is_pruned):
        kindname = entity.kindname()
        if "Function" in kindname:
            if is_pruned:
                return_type = entity.type() or "void"
                parameters = entity.parameters() or "void"
                func_name = entity.longname()
                dependency = f"{return_type} {func_name}({parameters})"
            else:
                dependency = entity.contents()

        elif "Macro" in kindname:
            if (entity.longname().startswith("assert") or
                    entity.longname().startswith("__ASSERT")):
                return None
            if entity.parameters() is not None:
                dependency = f"#define {entity.longname()}({entity.parameters()}) {entity.value()}"
            elif entity.value() is not None:
                dependency = f"#define {entity.longname()} {entity.value()}"
            else:
                dependency = f"#define {entity.longname()}"

        elif "Typedef" in kindname:
            dependency = f"typedef {entity.type()} {entity.longname()}"

        elif "Namespace" in kindname:
            if len(entity.contents()) == 0:
                dependency = f"namespace {entity.longname()}"
            else:
                dependency = self.prune_function_implementation(entity.contents()) if is_pruned else entity.contents()

        elif "Class" in kindname:
            dependency = self.prune_function_implementation(entity.contents()) if is_pruned else entity.contents()

        elif "Struct" in kindname:
            dependency = self.prune_function_implementation(entity.contents()) if is_pruned else entity.contents()

        elif "Type" in kindname:
            dependency = entity.longname()

        elif "Enum" in kindname:
            dependency = entity.contents()

        elif "Object" in kindname or "Parameter" in kindname or "File" in kindname:
            dependency = None

        else:
            print("type:", kindname, entity.type(), entity.name(), entity.contents(), entity.value())
            dependency = None

        return dependency

    def _normalize_kindname(self, kindname: str) -> str:
        for k in ("Function", "Macro", "Typedef", "Namespace", "Class", "Struct",
                  "Type", "Enum", "Object", "Parameter", "File"):
            if k in kindname:
                return k
        return kindname

    def get_file_all_dependencies(self, file_path, max_layer=1, is_pruned=True):
        file_ent = self.db.lookup(file_path, "File")[0]

        all_kind = set()
        all_dependencies: dict[str, set] = {}

        def get_dependencies_recursive(entity, visited, layer):
            if layer > max_layer:
                return
            if entity.id() in visited:
                return
            visited.add(entity.id())

            for ref in entity.refs():
                dep_ent = ref.ent()
                dependency = self.format_dependency_info(dep_ent, is_pruned)
                if dependency is not None:
                    all_kind.add(dep_ent.kindname())
                    normalized = self._normalize_kindname(dep_ent.kindname())
                    all_dependencies.setdefault(normalized, set()).add(dependency)
                get_dependencies_recursive(dep_ent, visited, layer + 1)

        get_dependencies_recursive(file_ent, set(), 1)
        for k in all_dependencies:
            all_dependencies[k] = sorted(all_dependencies[k])
        return all_dependencies, sorted(all_kind)

    def get_function_declaration(self, func_name, kind="Function", is_pruned=True):
        func_ent = self.db.lookup(func_name, kind)[0]
        return self.format_dependency_info(func_ent, is_pruned)

    def get_function_all_dependencies(self, func_name, kind="Function", is_pruned=True):
        dependencies_dict: dict[str, set] = {}
        func_ent = self.db.lookup(func_name, kind)[0]
        for ref in func_ent.refs():
            if func_name == ref.ent().longname():
                continue
            normalized = self._normalize_kindname(ref.ent().kindname())
            dependency = self.format_dependency_info(ref.ent(), is_pruned)
            if dependency is None or len(dependency) == 0:
                continue
            dependencies_dict.setdefault(normalized, set()).add(dependency)

        for k in dependencies_dict:
            dependencies_dict[k] = sorted(dependencies_dict[k])
        return dependencies_dict

    def get_data_control_content(self, analyze_file, function_name, row=None):
        db = self.db
        content = {}
        vis = set()
        for file in db.ents("File"):
            abs_path = file.longname()
            if abs_path != analyze_file:
                continue
            file_name = os.path.basename(abs_path)

            start = math.inf
            end = -math.inf
            for ref in file.filerefs():
                if function_name != ref.ent().name():
                    continue
                kind = ref.kindname()
                if kind == "Define":
                    start = min(start, ref.line())
                elif kind == "End":
                    end = max(end, ref.line())

            if end == -math.inf and row is not None:
                end = start + row

            # Guard: if function was not found in this file, skip the second pass
            if math.isinf(start) or math.isinf(end):
                continue

            target_lines_set = set(range(int(start) - 1, int(end) + 1))

            for ref in file.filerefs():
                ref_ent_name = ref.ent().name()
                if ref_ent_name == file_name:
                    continue
                ref_line = ref.line()
                if ref_line not in target_lines_set or ref.scope().kindname() == "File":
                    continue
                if ref.isforward():
                    continue
                kind = ref.kindname()
                if kind in ("Begin", "End"):
                    continue

                sc = ref.scope()
                if sc.kindname() == "Macro":
                    ct = f"#define {sc.longname()} {sc.value()}" if sc.value() else f"#define {sc.longname()}"
                else:
                    ct = sc.contents()

                while sc and sc.kindname() != "File":
                    sc = sc.parent()

                if len(ct) > 0 and ct not in vis:
                    vis.add(ct)
                    if not sc:
                        continue
                    name = sc.longname()
                    content[name] = content.get(name, "") + ("\n" if name in content else "") + ct

        return content

    # ------------------------------------------------------------------ #
    #  Tool interface                                                      #
    # ------------------------------------------------------------------ #

    @override
    def get_model_provider(self) -> str | None:
        return self._model_provider

    @override
    def get_name(self) -> str:
        return "understand_toolkit"

    @override
    def get_description(self) -> str:
        return (
            "Static code analysis tool powered by SciTools Understand. "
            "Provides database codebase dependency information for plan_agent, including "
            "directory structure, function source content, function/file dependency graphs, "
            "data/control-flow content, and SQLite built-in function registration lookup. "
            f"Available commands: {', '.join(_COMMANDS)}."
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="command",
                type="string",
                description=(
                    f"The analysis command to run. One of: {', '.join(_COMMANDS)}. "
                    "get_directory_structure: returns project directory tree (no extra params). "
                    "get_function_content: returns source of given functions (needs file_paths, function_names). "
                    "get_function_declaration: returns declaration of a function (needs func_name). "
                    "get_function_dependencies: returns all dependencies of a function (needs func_name). "
                    "get_file_dependencies: returns all dependencies of a file (needs file_path). "
                    "get_data_control_content: returns data/control-flow context for a function (needs file_path, function_name). "
                    "find_func_by_re: finds SQLite C implementation name for a built-in keyword (needs keyword)."
                ),
                required=True,
                enum=_COMMANDS,
            ),
            ToolParameter(
                name="func_name",
                type="string",
                description="Function name for get_function_declaration and get_function_dependencies.",
            ),
            ToolParameter(
                name="file_path",
                type="string",
                description="Absolute file path for get_file_dependencies and get_data_control_content.",
            ),
            ToolParameter(
                name="function_name",
                type="string",
                description="Function name for get_data_control_content.",
            ),
            ToolParameter(
                name="file_paths",
                type="string",
                description="Comma-separated absolute file paths for get_function_content (parallel with function_names).",
            ),
            ToolParameter(
                name="function_names",
                type="string",
                description="Comma-separated function names for get_function_content (parallel with file_paths).",
            ),
            ToolParameter(
                name="keyword",
                type="string",
                description="SQLite built-in function name (e.g. 'substr') for find_func_by_re.",
            ),
            ToolParameter(
                name="max_layer",
                type="integer",
                description="Recursion depth for get_file_dependencies (default: 1).",
            ),
            ToolParameter(
                name="is_pruned",
                type="boolean",
                description="Whether to return function declarations only instead of full implementations (default: true).",
            ),
        ]

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        command = arguments.get("command", "")

        try:
            if command == "get_directory_structure":
                result = self.get_directory_structure()
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            elif command == "get_function_content":
                file_paths_raw = arguments.get("file_paths", "")
                function_names_raw = arguments.get("function_names", "")
                file_paths = [p.strip() for p in file_paths_raw.split(",") if p.strip()]
                function_names = [f.strip() for f in function_names_raw.split(",") if f.strip()]
                if len(file_paths) != len(function_names):
                    return ToolExecResult(output="Error: file_paths and function_names must have the same number of entries.")
                result = self.get_function_content(file_paths, function_names)
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            elif command == "get_function_declaration":
                func_name = arguments.get("func_name", "")
                if not func_name:
                    return ToolExecResult(output="Error: func_name is required.")
                result = self.get_function_declaration(func_name)
                return ToolExecResult(output=result or "")

            elif command == "get_function_dependencies":
                func_name = arguments.get("func_name", "")
                if not func_name:
                    return ToolExecResult(output="Error: func_name is required.")
                is_pruned = arguments.get("is_pruned", True)
                result = self.get_function_all_dependencies(func_name, is_pruned=is_pruned)
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            elif command == "get_file_dependencies":
                file_path = arguments.get("file_path", "")
                if not file_path:
                    return ToolExecResult(output="Error: file_path is required.")
                max_layer = int(arguments.get("max_layer", 1))
                is_pruned = arguments.get("is_pruned", True)
                result, _ = self.get_file_all_dependencies(file_path, max_layer=max_layer, is_pruned=is_pruned)
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            elif command == "get_data_control_content":
                file_path = arguments.get("file_path", "")
                function_name = arguments.get("function_name", "")
                if not file_path or not function_name:
                    return ToolExecResult(output="Error: file_path and function_name are required.")
                result = self.get_data_control_content(file_path, function_name)
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            elif command == "find_func_by_re":
                keyword = arguments.get("keyword", "")
                if not keyword:
                    return ToolExecResult(output="Error: keyword is required.")
                result = self.find_func_by_re(keyword)
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            else:
                return ToolExecResult(output=f"Unknown command: '{command}'. Available: {', '.join(_COMMANDS)}")

        except Exception as e:
            return ToolExecResult(output=f"Error executing '{command}': {e}")


if __name__ == "__main__":
    tool = UnderstandTool(language="C++", project_root="/data/wei/code/sqlite")

    # Example: get directory structure
    # print(json.dumps(tool.get_directory_structure(), indent=2))

    # Example: get function dependencies
    # deps = tool.get_function_all_dependencies("text_substring")
    # print(deps)

    # Example: get file dependencies
    file_path = "/data/wei/code/sqlite/src/func.c"
    deps, kinds = tool.get_file_all_dependencies(file_path=file_path)
    print(deps)
