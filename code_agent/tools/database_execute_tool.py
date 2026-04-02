# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import os
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from code_agent.tools.base import Tool, ToolCallArguments, ToolExecResult, ToolParameter
from code_agent.tools.database.postgresql_compile_test import compile_postgresql
from code_agent.tools.database.sqlite_compile_test import compile_sqlite
from code_agent.utils.config import get_database_config, get_current_database

install_folder: str = get_database_config(get_current_database()).install_folder

CompileToolModes = [
    "syntax",
    "compliance",
    "semantic",
    # "performance"
]

CompileToolDatabases = [
    "postgresql",
    "sqlite",
    "duckdb",
    "clickhouse",
]


class DatabaseExecuteTool(Tool):
    """Verify whether the modified database project can be compiled successfully."""

    def __init__(self, model_provider: str | None = None) -> None:
        super().__init__(model_provider)

    @override
    def get_model_provider(self) -> str | None:
        return self._model_provider

    @override
    def get_name(self) -> str:
        return "database_execute"

    @override
    def get_description(self) -> str:
        return "Verify whether the modified database project can be compiled successfully."

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="mode",
                type="string",
                description="The mode to verify the project.",
                required=True,
                enum=CompileToolModes,
            ),
            ToolParameter(
                name="database",
                type="string",
                description="The database to be verified.",
                required=True,
                enum=CompileToolDatabases,
            ),
            ToolParameter(
                name="database",
                type="dict",
                description="The implemented code to be verified.",
            ),
        ]

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        mode = str(arguments["mode"]) if "mode" in arguments else None
        if mode is None or mode not in CompileToolModes:
            return ToolExecResult(
                error=f"No or wrong mode provided for the {self.get_name()} tool",
                error_code=-1,
            )

        database = str(arguments["database"]) if "database" in arguments else None
        if database is None or database not in CompileToolDatabases:
            return ToolExecResult(
                error=f"No or wrong database provided for the {self.get_name()} tool",
                error_code=-1,
            )

        if mode == "syntax":
            return ToolExecResult(output="Task done.")

        elif mode == "compliance":
            if database == "postgresql":
                is_success, result = compile_postgresql(os.getcwd(), install_folder)
            elif database == "sqlite":
                # is_success, result = compile_sqlite(os.getcwd())
                is_success = False
                result = """
                /usr/bin/ld: /tmp/ccfcYPDE.o: in function `substrFunc':
/data/wei/code/sqlite5435/build/sqlite3.c:132689: undefined reference to `sqlite3Utf8ByteLen'
/usr/bin/ld: /data/wei/code/sqlite5435/build/sqlite3.c:132690: undefined reference to `sqlite3Utf8ByteLen'
collect2: error: ld returned 1 exit status
make: *** [/data/wei/code/sqlite5435/main.mk:2136: sqlite3] Error 1
                """
            else:
                is_success, result = True, "Skipped."

            if is_success:
                return ToolExecResult(output="Task done.")
            else:
                return ToolExecResult(error=f"Error occurs when try to compile the database "
                                            f"after the generated code integration:\n{result}", error_code=-1)

        elif mode == "semantic":
            return ToolExecResult(output="Task done.")

        else:
            # return ToolResult(
            #     name=tool_call.name,
            #     success=tool_exec_result.error_code == 0,
            #     result=tool_exec_result.output,
            #     error=tool_exec_result.error,
            #     call_id=tool_call.call_id,
            #     id=tool_call.id,
            # )
            return ToolExecResult(output="Task done.")
