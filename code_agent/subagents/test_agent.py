# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""TestAgent for validating project-specific codebase personalization."""
import json
import os
import re
import asyncio
import contextlib
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from code_agent.agent.agent_basics import AgentExecution, AgentStep
from code_agent.agent.base_agent import BaseAgent

from code_agent.tools import tools_registry
from code_agent.tools.base import Tool, ToolResult, ToolCallArguments, ToolExecResult
from code_agent.tools.database_verify_tool import DatabaseVerifyTool
from code_agent.utils.config import MCPServerConfig, AgentRunConfig
from code_agent.utils.demo_mode import DEMO_RECORDING_MODE
from code_agent.utils.llm_clients.llm_basics import LLMMessage, LLMResponse
from code_agent.utils.mcp_client import MCPClient
from code_agent.prompt.agent_prompt import SYSTEM_PROMPT_TEST_AGENT, USER_PROMPT_TEST_AGENT

TestAgentToolNames = [
    # "str_replace_based_edit_tool",
    "sequentialthinking",
    # "json_edit_tool",
    "task_done",
    # "bash",
]


class TestAgent(BaseAgent):
    """Test Agent specialized in validating project-specific codebase personalization."""

    def __init__(
            self,
            test_agent_config: AgentRunConfig,
            agent_type: str = "test_agent",
    ):
        """Initialize TestAgent.

        Args:
            config: Configuration object containing model parameters and other settings.
                   Required if llm_client is not provided.
            llm_client: Optional pre-configured LLMClient instance.
                       If provided, it will be used instead of creating a new one from config.
        """
        self.project_path: str = ""
        self.base_commit: str | None = None
        self.must_patch: str = "false"
        self.patch_path: str | None = None
        self.mcp_servers_config: dict[str, MCPServerConfig] | None = (
            test_agent_config.mcp_servers_config if test_agent_config.mcp_servers_config else None
        )
        self.allow_mcp_servers: list[str] | None = (
            test_agent_config.allow_mcp_servers if test_agent_config.allow_mcp_servers else []
        )
        self.mcp_tools: list[Tool] = []
        self.mcp_clients: list[MCPClient] = []  # Keep track of MCP clients for cleanup
        self.agent_type = agent_type

        self._semantic_prompted: bool = False  # True after first semantic instruction is sent

        super().__init__(
            agent_config=test_agent_config, agent_type=agent_type
        )

    async def initialise_mcp(self):
        """Async factory to create and initialize TestAgent."""
        await self.discover_mcp_tools()

        if self.mcp_tools:
            self._tools.extend(self.mcp_tools)

    async def discover_mcp_tools(self):
        if self.mcp_servers_config:
            for mcp_server_name, mcp_server_config in self.mcp_servers_config.items():
                if self.allow_mcp_servers is None:
                    return
                if mcp_server_name not in self.allow_mcp_servers:
                    continue
                mcp_client = MCPClient()
                try:
                    await mcp_client.connect_and_discover(
                        mcp_server_name,
                        mcp_server_config,
                        self.mcp_tools,
                        self._llm_client.provider.value,
                    )
                    # Store client for later cleanup
                    self.mcp_clients.append(mcp_client)
                except Exception:
                    # Clean up failed client
                    with contextlib.suppress(Exception):
                        await mcp_client.cleanup(mcp_server_name)
                    continue
                except asyncio.CancelledError:
                    # If the task is cancelled, clean up and skip this server
                    with contextlib.suppress(Exception):
                        await mcp_client.cleanup(mcp_server_name)
                    continue
        else:
            return

    @override
    def new_task(
            self,
            task: dict,
            extra_args: dict[str, str] | None = None,
            tool_names: list[str] | None = None,
            implemented_code: str = None,
            self_testcase: str = None,
            other_testcase: str = None,
    ):
        """Create a new task."""
        self._task: dict = task
        self._extra_args: dict = extra_args
        if tool_names is None and len(self._tools) == 0:
            tool_names = TestAgentToolNames

            # Get the model provider from the LLM client
            provider = self._model_config.model_provider.provider
            self._tools: list[Tool] = [
                tools_registry[tool_name](model_provider=provider) for tool_name in tool_names
            ]

        self._initial_messages: list[LLMMessage] = []
        self._initial_messages.append(
            LLMMessage(role="system", content=self.get_system_prompt()))
        self._initial_messages.append(LLMMessage(role="user",
                                                  content=self.get_initial_user_prompt(
                                                      implemented_code=implemented_code,
                                                      self_testcase=self_testcase,
                                                      other_testcase=other_testcase)))

        # If trajectory recorder is set, start recording
        if self._trajectory_recorder:
            self._trajectory_recorder.start_recording(
                agent_type=self.agent_type,
                task=task,
                provider=self._llm_client.provider.value,
                model=self._model_config.model,
                max_steps=self._max_steps,
            )

    @override
    async def execute_task(self, tools=None) -> AgentExecution:
        """Execute the task and finalize trajectory recording."""
        execution = await super().execute_task(tools)

        # Finalize trajectory recording if recorder is available
        if self._trajectory_recorder:
            self._trajectory_recorder.finalize_recording(
                agent_type=self.agent_type,
                success=execution.success, final_result=execution.final_result
            )

        return execution

    def get_system_prompt(self) -> str:
        """Get the system prompt for TestAgent."""
        return SYSTEM_PROMPT_TEST_AGENT.format(database=self.db_name[self._task["database"]]).strip()

    def get_initial_user_prompt(self, implemented_code=None, self_testcase=None, other_testcase=None) -> str:
        """Get the initial user prompt for TestAgent."""
        self.self_specification = self.format_specification(self._task["func_name"],
                                                            self._task["description"], self._task["example"])
        self.other_specification = self.get_other_specification()

        return USER_PROMPT_TEST_AGENT.format(
            database=self.db_name[self._task["database"]], directory=self._task["directory"],
            func_name=self._task["func_name"], self_specification=self.self_specification,
            implemented_code=implemented_code, self_testcase=self_testcase, other_testcase=other_testcase,
        ).strip()

    @override
    def reflect_on_result(self, tool_results: list[ToolResult]) -> str | None:
        return None

    async def check_syntax(self, database: str, base_commit: str = "HEAD") -> ToolExecResult:
        """Run static syntax analysis on modified files for the given database."""
        verify_tool = DatabaseVerifyTool()
        return await verify_tool.execute(
            ToolCallArguments({"mode": "syntax", "database": database, "base_commit": base_commit})
        )

    async def check_compliance(self, database: str) -> ToolExecResult:
        """Run full build compliance check for the given database."""
        verify_tool = DatabaseVerifyTool()
        return await verify_tool.execute(
            ToolCallArguments({"mode": "compliance", "database": database})
        )

    async def run_verification(self, task: dict, extra_args: dict) -> ToolExecResult:
        """Run validation.

        batch_function: three-stage database validation (syntax → compliance → semantic).
        interactive_chat: prompt the caller to run project tests via bash.
        """
        if task.get("task_kind") == "interactive_chat":
            user_input = task.get("user_input", "the current task")
            return ToolExecResult(
                output=(
                    f"Verification guidance for '{user_input}':\n"
                    f"1. Run the project's test suite (e.g., pytest, make test, cargo test).\n"
                    f"2. Manually test the changed functionality.\n"
                    f"3. Confirm the implementation matches the stated requirements.\n"
                    f"Use bash to execute tests. Call task_done after confirming correctness."
                ),
            )

        database = task["database"]
        base_commit = extra_args.get("base_commit", "HEAD") if extra_args else "HEAD"

        syntax_result = await self.check_syntax(database, base_commit)
        if syntax_result.error is not None:
            return ToolExecResult(
                error=f"Syntax check failed:\n{syntax_result.error}", error_code=-1
            )

        compliance_result = await self.check_compliance(database)
        if compliance_result.error is not None:
            return ToolExecResult(
                error=f"Compliance check failed:\n{compliance_result.error}", error_code=-1
            )

        if DEMO_RECORDING_MODE:
            # TEMP(demo): skip the second task_done round-trip so recorded demos
            # can end in one pass. Disable demo_mode.py to restore the guard.
            self._semantic_prompted = False
            return ToolExecResult(
                output="Validation passed (demo mode, semantic re-check skipped temporarily)."
            )

        if not self._semantic_prompted:
            self._semantic_prompted = True
            func_name = task.get("func_name", "the implemented function")
            file_path = task.get("file_path", "the target file")
            return ToolExecResult(
                error=(
                    f"Syntax and compliance checks passed for `{func_name}`.\n"
                    f"Please verify semantic correctness before marking the task done:\n"
                    f"1. Use bash (or other tools) to run relevant tests against {file_path}.\n"
                    f"2. Review the implementation logic and edge cases.\n"
                    f"Call task_done again only after confirming semantic correctness."
                ),
                error_code=-1,
            )

        self._semantic_prompted = False  # reset for potential task reuse
        return ToolExecResult(output="Validation passed (syntax, compliance, semantic).")

    def parse_test_agent_output(self, llm_output) -> (bool, str):
        try:
            if "```json" in llm_output:
                pattern = r"```json\s*([\s\S]*?)\s*```"
            else:
                pattern = r"```\s*([\s\S]*?)\s*```"

            match = re.search(pattern, llm_output, re.DOTALL)
            if match:
                test = json.loads(match.group(1).strip())["Testcase"]
            else:
                test = json.loads(llm_output.replace("```json", "")
                                  .replace("```", "").strip())["Testcase"]

            return True, test
        except Exception as e:
            print(f"Invalid JSON format: {e}")
            return False, {"Error": str(e)}

    @override
    def llm_indicates_task_completed(self, llm_response: LLMResponse) -> (bool, dict):
        """Check if the LLM indicates that the task is completed."""
        # TODO: to be removed.
        format_check, testcase = self.parse_test_agent_output(llm_response.content.replace("Code", "Testcase"))
        return format_check, testcase

    @override
    async def _is_task_completed(self, llm_response: LLMResponse, step: AgentStep = None) -> (bool, list[LLMMessage]):
        """Enhanced task completion detection."""
        return True, [LLMMessage(role="user", content="Testcase received.")]

    def cleanup_test_artifacts(self) -> None:
        """Clean up test artifact files left by the test runner."""
        working_dir = os.getcwd()
        artifacts = [
            f"{working_dir}/build/test-out.txt",                        # sqlite
            f"{working_dir}/src/test/regress/regression.diffs",         # postgresql
        ]
        for path in artifacts:
            if os.path.exists(path):
                with contextlib.suppress(Exception):
                    os.remove(path)

    @override
    async def cleanup_mcp_clients(self) -> None:
        """Clean up all MCP clients to prevent async context leaks."""
        for client in self.mcp_clients:
            with contextlib.suppress(Exception):
                await client.cleanup("cleanup")
        self.mcp_clients.clear()
