# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""dep_tool.py — Lightweight code dependency analysis tool.

Drop-in replacement for UnderstandTool (SciTools Understand).
Supports C, C++, and Python using only Python stdlib (ast, re, os).

Commands (same interface as UnderstandTool):
  get_directory_structure  — project directory tree
  get_function_content     — source of named functions
  get_function_declaration — signature of a named function
  get_function_dependencies — what a function calls / uses
  get_file_dependencies    — #include / import dependencies of a file
  find_func_by_re          — SQLite built-in function registration lookup
"""

import ast
import json
import os
import re
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from code_agent.tools.base import Tool, ToolCallArguments, ToolExecResult, ToolParameter

_COMMANDS = [
    "get_directory_structure",
    "get_function_content",
    "get_function_declaration",
    "get_function_dependencies",
    "get_file_dependencies",
    "find_func_by_re",
]

_C_EXTENSIONS = frozenset({".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".c++"})
_PY_EXTENSIONS = frozenset({".py"})

# Keywords that can never be function names in C/C++
_C_KEYWORDS = frozenset({
    "if", "else", "for", "while", "do", "switch", "case", "default",
    "break", "continue", "return", "goto", "sizeof", "typeof",
    "struct", "union", "enum", "typedef", "class", "namespace",
    "template", "try", "catch", "throw", "new", "delete", "operator",
    "__attribute__", "__declspec", "__typeof__", "__extension__",
})


# ─────────────────────────────────────────────────────────────────────────────
# C / C++ helpers
# ─────────────────────────────────────────────────────────────────────────────

def _preprocess_c(src: str) -> str:
    """Strip C/C++ comments and replace string/char literal contents with spaces.

    Newlines are preserved so that line numbers in the result match the
    original source.  After this pass it is safe to count braces without
    worrying about '{' / '}' inside comments or string literals.
    """
    result: list[str] = []
    i = 0
    n = len(src)

    while i < n:
        c = src[i]

        # ── string literal ──────────────────────────────────────────────────
        if c == '"':
            result.append('"')
            i += 1
            while i < n:
                c2 = src[i]
                if c2 == '\\':
                    result.append('  ')  # two chars → two spaces
                    i += 2
                elif c2 == '"':
                    result.append('"')
                    i += 1
                    break
                elif c2 == '\n':
                    result.append('\n')  # preserve line break
                    i += 1
                else:
                    result.append(' ')
                    i += 1

        # ── char literal ────────────────────────────────────────────────────
        elif c == "'":
            result.append("'")
            i += 1
            while i < n:
                c2 = src[i]
                if c2 == '\\':
                    result.append('  ')
                    i += 2
                elif c2 == "'":
                    result.append("'")
                    i += 1
                    break
                elif c2 == '\n':
                    result.append('\n')
                    i += 1
                else:
                    result.append(' ')
                    i += 1

        # ── line comment ────────────────────────────────────────────────────
        elif src[i:i+2] == '//':
            while i < n and src[i] != '\n':
                result.append(' ')
                i += 1

        # ── block comment ───────────────────────────────────────────────────
        elif src[i:i+2] == '/*':
            i += 2
            while i < n:
                if src[i] == '\n':
                    result.append('\n')
                    i += 1
                elif src[i:i+2] == '*/':
                    i += 2
                    break
                else:
                    result.append(' ')
                    i += 1

        else:
            result.append(c)
            i += 1

    return ''.join(result)


def _extract_func_name(context: str) -> str | None:
    """Return the function name from text that precedes a '{', or None.

    Strategy: find the first ``identifier(`` pattern (left-to-right) where
    the identifier is not a C/C++ keyword, and where there is no ';' between
    the last '}' and this identifier (which would indicate a separate
    statement rather than a function signature).

    Also rejects '#define' macros.
    """
    if '(' not in context:
        return None
    # Reject preprocessor macros
    if re.search(r'#\s*define\b', context):
        return None

    for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', context):
        name = m.group(1)
        if name in _C_KEYWORDS:
            continue
        # Check that no ';' appears between the previous '}' and this name
        preceding = context[:m.start()]
        last_brace = preceding.rfind('}')
        last_semi = preceding.rfind(';')
        if last_semi > last_brace:
            # A ';' after the last '}' means this is a new statement — skip
            continue
        return name

    return None


def _scan_c_functions(src: str) -> list[dict]:
    """Scan C/C++ source for top-level function definitions.

    Returns a list of dicts, each with keys:
      name, signature, body, start_line, end_line   (all 1-based line numbers)
    """
    preprocessed = _preprocess_c(src)
    orig_lines = src.split('\n')
    prep_lines = preprocessed.split('\n')

    functions: list[dict] = []
    brace_depth = 0
    pending: list[str] = []      # preprocessed lines accumulated at depth 0
    pending_start = 0            # 0-based index of first pending line
    current_func: dict | None = None

    for lineno, line in enumerate(prep_lines):

        if brace_depth == 0:
            opens = line.count('{')
            closes = line.count('}')

            if opens > 0:
                # There is at least one '{' on this line.
                context = ' '.join(pending[-10:]) + ' ' + line

                if opens > closes:
                    # ── Normal case: scope opens, doesn't close on same line ─
                    func_name = _extract_func_name(context)
                    if func_name:
                        sig_raw = context.rsplit('{', 1)[0].strip()
                        start_line = (pending_start + 1) if pending else (lineno + 1)
                        current_func = {
                            'name': func_name,
                            'signature': re.sub(r'\s+', ' ', sig_raw),
                            'start_line': start_line,
                        }
                    brace_depth += opens - closes

                else:
                    # ── Balanced: one-liner  { ... }  on a single line ──────
                    func_name = _extract_func_name(context)
                    if func_name:
                        sig_raw = context.split('{', 1)[0].strip()
                        functions.append({
                            'name': func_name,
                            'signature': re.sub(r'\s+', ' ', sig_raw),
                            'body': orig_lines[lineno],
                            'start_line': lineno + 1,
                            'end_line': lineno + 1,
                        })

                # Either way, reset context window
                pending = []
                pending_start = lineno + 1

            else:
                # No '{' on this line — accumulate context
                if not pending:
                    pending_start = lineno
                pending.append(line)
                # A ';' at depth 0 ends a declaration/statement; start fresh
                if ';' in line:
                    pending = []
                    pending_start = lineno + 1

        else:
            # ── Inside a scope ────────────────────────────────────────────────
            brace_depth += line.count('{') - line.count('}')

            if brace_depth == 0:
                if current_func is not None:
                    current_func['end_line'] = lineno + 1
                    s = current_func['start_line'] - 1
                    e = current_func['end_line']
                    current_func['body'] = '\n'.join(orig_lines[s:e])
                    functions.append(current_func)
                    current_func = None
                # Reset context window after any closed scope
                pending = []
                pending_start = lineno + 1

    return functions


def _find_c_includes(src: str) -> list[str]:
    """Return sorted, deduplicated #include targets from C/C++ source."""
    includes = []
    for m in re.finditer(r'^\s*#\s*include\s*([<"][^>"]+[>"])', src, re.MULTILINE):
        includes.append(m.group(1))
    return sorted(set(includes))


def _find_calls_in_text(text: str) -> set[str]:
    """Return names of all identifiers called as functions in *text*."""
    calls: set[str] = set()
    for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', text):
        name = m.group(1)
        if name not in _C_KEYWORDS:
            calls.add(name)
    return calls


def _find_macros_in_text(text: str) -> set[str]:
    """Return UPPER_CASE identifiers (likely macros) appearing in *text*."""
    macros: set[str] = set()
    for m in re.finditer(r'\b([A-Z][A-Z0-9_]{2,})\b', text):
        macros.add(m.group(1))
    return macros


# ─────────────────────────────────────────────────────────────────────────────
# Python helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scan_py_functions(src: str) -> list[dict]:
    """Return function info dicts from Python source using the ast module."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    orig_lines = src.split('\n')
    results: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = node.lineno
        end = node.end_lineno or node.lineno
        body = '\n'.join(orig_lines[start - 1:end])

        # Build a readable signature
        args = node.args
        params: list[str] = []
        # positional args (possibly with defaults)
        defaults_offset = len(args.args) - len(args.defaults)
        for idx, arg in enumerate(args.args):
            annotation = ''
            if arg.annotation:
                annotation = ': ' + ast.unparse(arg.annotation)
            default = ''
            di = idx - defaults_offset
            if di >= 0:
                default = '=' + ast.unparse(args.defaults[di])
            params.append(f"{arg.arg}{annotation}{default}")
        if args.vararg:
            params.append(f"*{args.vararg.arg}")
        for kw in args.kwonlyargs:
            params.append(kw.arg)
        if args.kwarg:
            params.append(f"**{args.kwarg.arg}")

        ret = ''
        if node.returns:
            ret = ' -> ' + ast.unparse(node.returns)
        prefix = 'async def' if isinstance(node, ast.AsyncFunctionDef) else 'def'
        signature = f"{prefix} {node.name}({', '.join(params)}){ret}"

        results.append({
            'name': node.name,
            'signature': signature,
            'body': body,
            'start_line': start,
            'end_line': end,
        })

    return results


def _find_py_imports(src: str) -> list[str]:
    """Return sorted, deduplicated import strings from Python source."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            for alias in node.names:
                imports.append(f"{mod}.{alias.name}" if mod else alias.name)

    return sorted(set(imports))


def _find_py_calls(func_src: str) -> set[str]:
    """Return names of functions called within Python function source."""
    try:
        tree = ast.parse(func_src)
    except SyntaxError:
        return set()

    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# Shared utilities
# ─────────────────────────────────────────────────────────────────────────────

def _lang(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _C_EXTENSIONS:
        return 'c'
    if ext in _PY_EXTENSIONS:
        return 'py'
    return ''


def _read(path: str) -> str:
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def _scan_file(path: str) -> list[dict]:
    lang = _lang(path)
    if not lang:
        return []
    src = _read(path)
    if lang == 'c':
        return _scan_c_functions(src)
    return _scan_py_functions(src)


# ─────────────────────────────────────────────────────────────────────────────
# DepTool
# ─────────────────────────────────────────────────────────────────────────────

class DepTool(Tool):
    """Lightweight static dependency analysis for C/C++ and Python.

    Replaces UnderstandTool (SciTools Understand) without requiring any
    external software.  Uses regex + brace-matching for C/C++ and the
    stdlib ``ast`` module for Python.

    The function index is built lazily on first use and cached for the
    lifetime of the tool instance.
    """

    def __init__(
        self,
        model_provider: str | None = None,
        project_root: str = '.',
        language: str | None = None,   # accepted but ignored (auto-detected)
    ) -> None:
        super().__init__(model_provider)
        self.project_root = os.path.abspath(project_root)
        # {func_name: [{name, signature, body, start_line, end_line, file}, ...]}
        self._index: dict[str, list[dict]] | None = None

    # ── index management ──────────────────────────────────────────────────────

    def _build_index(self) -> None:
        self._index = {}
        for dirpath, dirnames, filenames in os.walk(self.project_root):
            # Skip hidden directories
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                if not _lang(fpath):
                    continue
                try:
                    for entry in _scan_file(fpath):
                        entry['file'] = fpath
                        self._index.setdefault(entry['name'], []).append(entry)
                except Exception:
                    pass  # skip unreadable files silently

    def _get_index(self) -> dict[str, list[dict]]:
        if self._index is None:
            self._build_index()
        return self._index

    def _lookup_func(self, func_name: str) -> list[dict]:
        return self._get_index().get(func_name, [])

    # ── command implementations ───────────────────────────────────────────────

    def get_directory_structure(self) -> dict:
        def traverse(dir_path: str) -> dict:
            contents: dict = {}
            try:
                for item in sorted(os.listdir(dir_path)):
                    item_path = os.path.join(dir_path, item)
                    if os.path.isfile(item_path):
                        contents[item] = None
                    elif os.path.isdir(item_path) and not item.startswith('.'):
                        contents[item] = traverse(item_path)
            except PermissionError:
                pass
            return contents
        return traverse(self.project_root)

    def get_function_content(self, files: list[str], functions: list[str]) -> dict:
        result: dict[str, str] = {}
        for fpath, fname in zip(files, functions):
            if not _lang(fpath):
                continue
            try:
                for entry in _scan_file(fpath):
                    if entry['name'] == fname:
                        result[fname] = entry['body']
                        break
            except Exception:
                pass
        return result

    def get_function_declaration(self, func_name: str) -> str:
        entries = self._lookup_func(func_name)
        if not entries:
            return f"Function '{func_name}' not found in project."
        e = entries[0]
        loc = f"  # {os.path.relpath(e['file'], self.project_root)}:{e['start_line']}"
        return e['signature'] + loc

    def get_function_dependencies(self, func_name: str, is_pruned: bool = True) -> dict:
        entries = self._lookup_func(func_name)
        if not entries:
            return {}
        e = entries[0]
        body = e['body']
        lang = _lang(e['file'])
        deps: dict[str, list[str]] = {}

        if lang == 'c':
            # ── function calls ──────────────────────────────────────────────
            calls = _find_calls_in_text(body) - {func_name}
            if calls:
                call_strs: list[str] = []
                for name in sorted(calls):
                    if is_pruned:
                        callee_entries = self._lookup_func(name)
                        call_strs.append(
                            callee_entries[0]['signature'] if callee_entries else name
                        )
                    else:
                        call_strs.append(name)
                deps['Function'] = call_strs

            # ── macro-like identifiers ──────────────────────────────────────
            macros = _find_macros_in_text(body)
            if macros:
                deps['Macro'] = sorted(macros)

            # ── file-level #includes ────────────────────────────────────────
            includes = _find_c_includes(_read(e['file']))
            if includes:
                deps['Include'] = includes

        elif lang == 'py':
            calls_py = _find_py_calls(body) - {func_name}
            if calls_py:
                call_strs_py: list[str] = []
                for name in sorted(calls_py):
                    if is_pruned:
                        callee_entries = self._lookup_func(name)
                        call_strs_py.append(
                            callee_entries[0]['signature'] if callee_entries else name
                        )
                    else:
                        call_strs_py.append(name)
                deps['Function'] = call_strs_py

            imports = _find_py_imports(_read(e['file']))
            if imports:
                deps['Import'] = imports

        return deps

    def get_file_dependencies(
        self, file_path: str, max_layer: int = 1, is_pruned: bool = True
    ) -> dict:
        lang = _lang(file_path)
        if not lang:
            return {}
        try:
            src = _read(file_path)
        except FileNotFoundError:
            return {'error': f"File not found: {file_path}"}

        deps: dict[str, list] = {}

        if lang == 'c':
            includes = _find_c_includes(src)
            if includes:
                deps['Include'] = includes
            funcs = _scan_c_functions(src)
            if funcs:
                deps['Function'] = [
                    f['signature'] if is_pruned else f['body'] for f in funcs
                ]

        elif lang == 'py':
            imports = _find_py_imports(src)
            if imports:
                deps['Import'] = imports
            funcs = _scan_py_functions(src)
            if funcs:
                deps['Function'] = [
                    f['signature'] if is_pruned else f['body'] for f in funcs
                ]

        return deps

    def find_func_by_re(self, key_word: str) -> list[str]:
        """Find SQLite C implementation name for a built-in keyword.

        Searches the standard SQLite function-registration files for
        FUNCTION / MFUNCTION / JFUNCTION macros.  Logic copied verbatim
        from UnderstandTool so results are identical.
        """
        func_loads = [
            os.path.join(self.project_root, 'src', 'func.c'),
            os.path.join(self.project_root, 'src', 'date.c'),
            os.path.join(self.project_root, 'src', 'json.c'),
            os.path.join(self.project_root, 'src', 'window.c'),
            os.path.join(self.project_root, 'src', 'alter.c'),
        ]
        content = ''
        for path in func_loads:
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    content += f.read() + '\n'
            except FileNotFoundError:
                continue

        pattern = rf'([A-Za-z_][A-Za-z0-9_]*)\(\s*({key_word}\s*,[\s\S]*?)\s*\)\s*,'
        matches = re.findall(pattern, content, re.DOTALL)
        key_functions: list[str] = []
        for typ, match in matches:
            item = match.split(',')
            if typ == 'MFUNCTION':
                if len(item) == 4:
                    key_functions.append(item[-1].strip())
            elif typ == 'JFUNCTION':
                if len(item) == 8:
                    key_functions.append(item[-1].strip())
            else:
                if len(item) >= 5:
                    is_numeric = all(
                        item[i].replace('-', '').strip().isdigit() for i in range(1, 4)
                    )
                    if is_numeric:
                        for i in range(4, len(item)):
                            if item[i].strip() and item[i].strip()[0].islower():
                                key_functions.append(item[i].strip())
        return key_functions

    # ── Tool interface ────────────────────────────────────────────────────────

    @override
    def get_name(self) -> str:
        return 'dep_toolkit'

    @override
    def get_description(self) -> str:
        return (
            'Lightweight static dependency analysis for C/C++ and Python. '
            'Drop-in replacement for understand_toolkit — requires no external tools. '
            'Uses regex + brace-matching for C/C++ and stdlib ast for Python. '
            'Provides directory structure, function content/declarations, '
            'call-graph dependencies, file include/import analysis, '
            'and SQLite built-in function registration lookup. '
            f'Available commands: {", ".join(_COMMANDS)}.'
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name='command',
                type='string',
                description=(
                    f'Analysis command. One of: {", ".join(_COMMANDS)}. '
                    'get_directory_structure: project tree (no extra params). '
                    'get_function_content: source of functions (needs file_paths, function_names). '
                    'get_function_declaration: signature of a function (needs func_name). '
                    'get_function_dependencies: call graph of a function (needs func_name). '
                    'get_file_dependencies: includes/imports of a file (needs file_path). '
                    'find_func_by_re: SQLite built-in lookup (needs keyword).'
                ),
                required=True,
                enum=_COMMANDS,
            ),
            ToolParameter(
                name='func_name',
                type='string',
                description='Function name for get_function_declaration and get_function_dependencies.',
                required=False,
            ),
            ToolParameter(
                name='file_path',
                type='string',
                description='Absolute file path for get_file_dependencies and get_data_control_content.',
                required=False,
            ),
            ToolParameter(
                name='file_paths',
                type='string',
                description='Comma-separated absolute file paths for get_function_content.',
                required=False,
            ),
            ToolParameter(
                name='function_names',
                type='string',
                description='Comma-separated function names for get_function_content (parallel with file_paths).',
                required=False,
            ),
            ToolParameter(
                name='keyword',
                type='string',
                description="SQLite built-in function name (e.g. 'substr') for find_func_by_re.",
                required=False,
            ),
            ToolParameter(
                name='max_layer',
                type='integer',
                description='Recursion depth for get_file_dependencies (default: 1).',
                required=False,
            ),
            ToolParameter(
                name='is_pruned',
                type='boolean',
                description='Return function declarations only instead of full bodies (default: true).',
                required=False,
            ),
        ]

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        command = str(arguments.get('command', ''))
        try:
            if command == 'get_directory_structure':
                result = self.get_directory_structure()
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            elif command == 'get_function_content':
                fps = [p.strip() for p in str(arguments.get('file_paths', '')).split(',') if p.strip()]
                fns = [f.strip() for f in str(arguments.get('function_names', '')).split(',') if f.strip()]
                if len(fps) != len(fns):
                    return ToolExecResult(
                        output='Error: file_paths and function_names must have the same number of entries.'
                    )
                result = self.get_function_content(fps, fns)
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            elif command == 'get_function_declaration':
                func_name = str(arguments.get('func_name', ''))
                if not func_name:
                    return ToolExecResult(output='Error: func_name is required.')
                return ToolExecResult(output=self.get_function_declaration(func_name))

            elif command == 'get_function_dependencies':
                func_name = str(arguments.get('func_name', ''))
                if not func_name:
                    return ToolExecResult(output='Error: func_name is required.')
                is_pruned = bool(arguments.get('is_pruned', True))
                result = self.get_function_dependencies(func_name, is_pruned=is_pruned)
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            elif command == 'get_file_dependencies':
                file_path = str(arguments.get('file_path', ''))
                if not file_path:
                    return ToolExecResult(output='Error: file_path is required.')
                max_layer = int(arguments.get('max_layer', 1))
                is_pruned = bool(arguments.get('is_pruned', True))
                result = self.get_file_dependencies(file_path, max_layer=max_layer, is_pruned=is_pruned)
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            elif command == 'find_func_by_re':
                keyword = str(arguments.get('keyword', ''))
                if not keyword:
                    return ToolExecResult(output='Error: keyword is required.')
                result = self.find_func_by_re(keyword)
                return ToolExecResult(output=json.dumps(result, ensure_ascii=False, indent=2))

            else:
                return ToolExecResult(
                    output=f"Unknown command '{command}'. Available: {', '.join(_COMMANDS)}"
                )

        except Exception as e:
            import traceback
            return ToolExecResult(
                output=f"Error executing '{command}': {e}\n{traceback.format_exc()}"
            )
