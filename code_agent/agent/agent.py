# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import asyncio
import contextlib
from enum import Enum

from code_agent.agent.agent_basics import AgentExecution
from code_agent.utils.cli.cli_console import CLIConsole
from code_agent.utils.config import AgentConfig, Config
from code_agent.utils.llm_clients.llm_basics import LLMMessage
from code_agent.utils.trajectory_recorder import TrajectoryRecorder

# from code_agent.vector_store.chroma_store import ChromaStore
# from code_agent.vector_store.embeddings import EmbeddingManager


class AgentType(Enum):
    PlanAgent = "plan_agent"
    CodeAgent = "code_agent"
    TestAgent = "test_agent"


class Agent:
    def __init__(
            self,
            agent_type: AgentType | str,
            config: Config,
            trajectory_file: str | None = None,
            cli_console: CLIConsole | None = None,
    ):
        if isinstance(agent_type, str):
            agent_type = AgentType(agent_type)
        self.agent_type: AgentType = agent_type

        # Set up trajectory recording
        if trajectory_file is not None:
            if isinstance(trajectory_file, TrajectoryRecorder):
                self.trajectory_file: str = str(trajectory_file.trajectory_path)
                self.trajectory_recorder: TrajectoryRecorder = trajectory_file
            else:
                self.trajectory_file: str = trajectory_file
                self.trajectory_recorder: TrajectoryRecorder = TrajectoryRecorder(trajectory_file)
        else:
            # Auto-generate trajectory file path
            self.trajectory_recorder = TrajectoryRecorder()
            self.trajectory_file = self.trajectory_recorder.get_trajectory_path()

        match self.agent_type:
            case AgentType.PlanAgent:
                if config.plan_agent is None:
                    raise ValueError("plan_agent_config is required for PlanAgent")
                from code_agent.subagents.plan_agent import PlanAgent

                self.agent_config: AgentConfig = config.plan_agent
                self.agent: PlanAgent = PlanAgent(
                    self.agent_config, agent_type=AgentType.PlanAgent.value
                )
                self.agent.set_cli_console(cli_console)
                self.agent.db_name = config.db_name

            case AgentType.CodeAgent:
                if config.code_agent is None:
                    raise ValueError("code_agent_config is required for CodeAgent")
                from .code_agent import CodeAgent

                self.agent_config: AgentConfig = config.code_agent
                self.agent: CodeAgent = CodeAgent(
                    self.agent_config, agent_type=AgentType.CodeAgent.value
                )
                self.agent.set_cli_console(cli_console)
                self.agent.db_name = config.db_name

            case AgentType.TestAgent:
                if config.test_agent is None:
                    raise ValueError("test_agent_config is required for TestAgent")
                from code_agent.subagents.test_agent import TestAgent

                self.agent_config: AgentConfig = config.test_agent
                self.agent: TestAgent = TestAgent(
                    self.agent_config, agent_type=AgentType.TestAgent.value
                )
                self.agent.set_cli_console(cli_console)
                self.agent.db_name = config.db_name

        self.agent.set_trajectory_recorder(self.trajectory_recorder)

        # self.vector_database = ChromaStore(persist_directory=config.vector_database.persist_directory)
        # model_config = {
        #     "name": config.embeddings.model,
        #     "model_provider": config.embeddings.model_provider.provider,
        #     "api_key": config.embeddings.model_provider.api_key,
        #     "base_url": config.embeddings.model_provider.base_url,
        # }
        # self.embedding_model = EmbeddingManager(model_name=config.embeddings.model,
        #                                         model_config=model_config)

    def close(self) -> None:
        """Shut down the underlying agent's private LLM executor.

        Delegates to BaseAgent.close() so that SessionRunner can release
        idle threads immediately after each turn without relying on GC.
        """
        self.agent.close()

    async def run(
            self,
            task: str | dict,
            extra_args: dict[str, str] | None = None,
            tool_names: list[str] | None = None,
    ):
        if isinstance(task, str):
            task = {
                "func_name": task, "description": task,
                "database": "sqlite",
            }
        # Normalize optional task fields with defaults at the convergence point
        # so all downstream agent code can safely use task["key"] directly.
        task.setdefault("description", "")
        task.setdefault("example", "")
        task.setdefault("directory", "")
        task.setdefault("file_path", "")
        task.setdefault("category", "")
        # self._task["func_name"], self._task["description"],
        # self._task["example"], file_path=self._task["file_path"]
        _skip_mcp = (extra_args or {}).get("skip_agent_mcp_bootstrap", False)

        await self.agent.new_task(task, extra_args, tool_names)

        if self.agent.allow_mcp_servers and not _skip_mcp:
            if self.agent.cli_console:
                self.agent.cli_console.print("Initialising MCP tools...")
            await self.agent.initialise_mcp()

        # In interactive_chat turns, begin_turn/end_turn manage the lifecycle;
        # print_task_details and start() are skipped to avoid duplicate output.
        _is_interactive = (
            isinstance(task, dict) and task.get("task_kind") == "interactive_chat"
        )

        if self.agent.cli_console and not _is_interactive:
            task_details = {
                "Task": task,
                "Model Provider": self.agent_config.model.model_provider.provider,
                "Model": self.agent_config.model.model,
                "Max Steps": str(self.agent_config.max_steps),
                "Trajectory File": self.trajectory_file,
                "Tools": ", ".join([tool.name for tool in self.agent.tools]),
            }
            _INTERNAL_EXTRA_ARGS = {
                "interactive_transcript", "session_mcp_tools",
                "skip_agent_mcp_bootstrap", "completion_policy",
                "memory_write_policy", "tool_profile",
                "chat_history_to_restore", "preserve_chat_history",
                "step_boundaries_to_restore",
            }
            if extra_args:
                for key, value in extra_args.items():
                    if key not in _INTERNAL_EXTRA_ARGS:
                        task_details[key.capitalize()] = str(value)
            self.agent.cli_console.print_task_details(task_details)

        cli_console_task = (
            asyncio.create_task(self.agent.cli_console.start())
            if self.agent.cli_console and not _is_interactive
            else None
        )

        # # TODO: to be removed for notest agent.
        # if self.agent_type.name == "CodeAgent":
        #     execution = AgentExecution(task=self.agent._task, steps=[])
        #     if len(self.agent.code_completed) != 0:
        #         execution.success = True
        #         execution.final_result = self.agent.code_completed
        #         execution.execution_time = 0
        #         print("execution.final_result", execution.final_result)
        #         return execution

        try:
            if len(self.agent.tools) == 0:
                execution = await self.agent.execute_task(tools=[])
            else:
                execution = await self.agent.execute_task(tools=self.agent.tools)
            if len(self.agent.tools) == 0 and self.agent_type.name == "CodeAgent":
                execution.final_result = self.agent.code_completed
        finally:
            # Ensure MCP cleanup happens even if execution fails.
            # Skip when SessionRunner manages the MCP lifecycle across turns.
            if not _skip_mcp:
                with contextlib.suppress(Exception):
                    await self.agent.cleanup_mcp_clients()
            # Clean up test artifacts (e.g. test-out.txt) left by the test runner.
            # TestAgent may be invoked directly or via TestSubagentTool inside CodeAgent.
            with contextlib.suppress(Exception):
                if self.agent_type == AgentType.TestAgent:
                    self.agent.cleanup_test_artifacts()
                elif self.agent_type == AgentType.CodeAgent and self.agent.test_agent is not None:
                    self.agent.test_agent.agent.cleanup_test_artifacts()

        if cli_console_task:
            await cli_console_task

        return execution
