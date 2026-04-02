# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""CodeAgent for project-specific codebase personalization."""
import os
import time
import uuid
import asyncio
import contextlib
import subprocess
from textwrap import dedent
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from code_agent.agent.agent_basics import AgentError, AgentExecution, AgentStep, AgentState, AgentStepState
from code_agent.agent.base_agent import BaseAgent
from code_agent.skills import SkillManager
from code_agent.memory import MemoryManager

from code_agent.tools import tools_registry
from code_agent.tools.base import Tool, ToolResult, ToolCall, ToolCallArguments
from code_agent.utils.config import MCPServerConfig, AgentRunConfig
from code_agent.utils.llm_clients.llm_basics import LLMMessage, LLMResponse
from code_agent.utils.mcp_client import MCPClient
from code_agent.prompt.agent_prompt import SYSTEM_PROMPT_CODE_AGENT, USER_PROMPT_CODE_AGENT

CodeAgentToolNames = [
    "skill",
    "str_replace_based_edit_tool",
    "sequentialthinking",
    "json_edit_tool",
    "task_done",
    "bash",
    "plan_subagent",
    "test_subagent",
]


class CodeAgent(BaseAgent):
    """Code Agent specialized for project-specific codebase personalization."""

    def __init__(
            self,
            code_agent_config: AgentRunConfig,
            agent_type: str = "code_agent",
    ):
        """Initialize CodeAgent.

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
            code_agent_config.mcp_servers_config if code_agent_config.mcp_servers_config else None
        )
        self.allow_mcp_servers: list[str] | None = (
            code_agent_config.allow_mcp_servers if code_agent_config.allow_mcp_servers else []
        )
        self.mcp_tools: list[Tool] = []
        self.mcp_clients: list[MCPClient] = []  # Keep track of MCP clients for cleanup
        self.agent_type = agent_type

        self._agent_skills_config = code_agent_config.skills
        self.skill_manager: SkillManager | None = None
        # True once the bootstrap attempt is done — does NOT imply skill_manager
        # is non-None or that discover() succeeded.
        self._skills_bootstrapped: bool = False

        self._memory_config = code_agent_config.memory
        self._memory_manager: MemoryManager | None = None
        self._memory_scopes: list[str] = []
        self._memory_context: dict = {}

        # newly added (subagent).
        self.plan_agent: None = None
        self.test_agent: None = None

        self.code_format = dict()
        self.code_completed = dict()

        super().__init__(
            agent_config=code_agent_config, agent_type=agent_type
        )

    def initialize_subagent(self, plan_agent, test_agent):
        self.plan_agent = plan_agent
        self.test_agent = test_agent

    async def initialise_mcp(self):
        """Async factory to create and initialize CodeAgent."""
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
    async def new_task(
            self,
            task: dict,
            extra_args: dict[str, str] | None = None,
            tool_names: list[str] | None = None,
    ):
        """Create a new task."""
        self._task: dict = task
        self._extra_args: dict = extra_args
        if not extra_args:
            raise AgentError("Project path and issue information are required.")
        if "project_path" not in extra_args:
            raise AgentError("Project path is required")
        optional_attrs_to_set = ["base_commit", "must_patch", "patch_path"]
        for attr in optional_attrs_to_set:
            if attr in extra_args:
                setattr(self, attr, extra_args[attr])

        # Invariant check: a CodeAgent instance is permanently bound to the
        # project_path of its first new_task() call.  Must run BEFORE the
        # assignment below so self.project_path still holds the old value.
        if self._skills_bootstrapped and self.project_path != extra_args.get("project_path", ""):
            raise RuntimeError(
                "CodeAgent instance cannot switch project_path after skills bootstrap. "
                "Create a new agent instance for a different project."
            )

        self.project_path = extra_args.get("project_path", "")
        task["directory"] = self.project_path

        task_kind = task.get("task_kind", "batch_function")

        # ① Tool selection — batch rebuilds from CodeAgentToolNames when empty;
        #   interactive uses tools already set from config (no rebuild needed).
        if task_kind == "batch_function":
            if tool_names is None and len(self._tools) == 0:
                tool_names = CodeAgentToolNames
                provider = self._model_config.model_provider.provider
                self._tools: list[Tool] = [
                    tools_registry[tool_name](model_provider=provider) for tool_name in tool_names
                ]

        # ② Extend with session MCP tools (applies to both task kinds).
        session_mcp = (extra_args or {}).get("session_mcp_tools", [])
        if session_mcp:
            self._tools.extend(session_mcp)
            self._rebuild_tool_executor(self._tools)

        # Skills bootstrap — runs ONCE per agent instance (session-static).
        if not self._skills_bootstrapped:
            if self._agent_skills_config and self._agent_skills_config.enabled:
                try:
                    self.skill_manager = SkillManager(
                        cwd=self.project_path,
                        config=self._agent_skills_config,
                    )
                    self.skill_manager.discover()
                except Exception:
                    import traceback
                    traceback.print_exc()
                    self.skill_manager = None
            self._skills_bootstrapped = True

        # ③ Wiring — skill manager and codeagent back-references (after tool list is final).
        if self.skill_manager is not None:
            skill_tool = next((t for t in self._tools if t.get_name() == "skill"), None)
            if skill_tool is not None:
                skill_tool._manager = self.skill_manager

        for tool in self._tools:
            if hasattr(tool, "_codeagent"):
                tool._codeagent = self

        # Reset per-task state on test_agent so stale flags from a previous task are cleared.
        if self.test_agent is not None and hasattr(self.test_agent.agent, "_semantic_prompted"):
            self.test_agent.agent._semantic_prompted = False

        # Initialise memory: scopes, project wisdom, past episodes, working memory
        self._memory_scopes = ["general"]
        if self._task.get("database"):
            self._memory_scopes.append(self._task["database"])

        if self._memory_config.enabled:
            if self._memory_manager is None:
                self._memory_manager = MemoryManager(
                    config=self._memory_config,
                    project_path=self.project_path,
                )
            self._memory_context = self._memory_manager.build_context(
                task=self._task,
                scopes=self._memory_scopes,
            )
            session_id = (
                self._trajectory_recorder.trajectory_path.stem
                if self._trajectory_recorder
                else f"session_{int(time.time())}"
            )
            self._memory_manager.init_working(
                session_id=session_id,
                model_context_window=self._model_config.context_window,
            )
        else:
            self._memory_context = {}

        # ④ Batch-only: specification formatting + plan subagent execution.
        if task_kind == "batch_function":
            self.self_specification = self.format_specification(
                self._task["func_name"],
                self._task["description"],
                self._task["example"],
                file_path=self._task["file_path"],
            )
            self.other_specification = self.get_other_specification()
            dependency = dict()
            self.dependency = self.format_dependency(dependency).strip()

            plan_tool = next((t for t in self._tools if t.get_name() == "plan_subagent"), None)
            if plan_tool is not None:
                plan_result = await plan_tool.execute({"number": self.plan_agent.agent._run_steps})
                plan_str = plan_result.output or ""
            else:
                plan_str = ""
        else:
            self.self_specification = self.other_specification = self.dependency = ""
            plan_str = ""

        # ⑤ Initial messages — batch uses existing system/user prompts;
        #   interactive restores full history (non-first turn) or builds first-turn prompt.
        if task_kind == "batch_function":
            self._initial_messages = [
                LLMMessage(role="system", content=self.get_system_prompt()),
                LLMMessage(role="user", content=self.get_initial_user_prompt(plan=plan_str)),
            ]
        else:
            from code_agent.session.render import build_interactive_first_turn
            from code_agent.session.store import _dict_to_llm_message
            chat_history_dicts = (extra_args or {}).get("chat_history_to_restore", [])
            if chat_history_dicts:
                # Non-first turn: restore full history; only the new user message is initial.
                history = [_dict_to_llm_message(d) for d in chat_history_dicts]
                self._llm_client.set_chat_history(history)
                self._agent_history = list(history)
                # Restore persisted step_boundaries so compaction guard windows are correct.
                # Fall back to [0, len(history)] only when no boundaries were persisted yet.
                restored_boundaries = (extra_args or {}).get("step_boundaries_to_restore", [])
                self._step_boundaries = restored_boundaries if restored_boundaries else [0, len(history)]
                self._initial_messages = [
                    LLMMessage(role="user", content=task.get("user_input", ""))
                ]
            else:
                # First turn: build complete [system, user] prompt.
                self._initial_messages = build_interactive_first_turn(
                    cwd=self.project_path or "",
                    user_input=task.get("user_input", ""),
                    system_addons=self._build_system_addons(),
                )
                # Append episodic user_addon (Layer B memory) to the user message,
                # mirroring the batch path in get_initial_user_prompt().
                if self._memory_context.get("user_addon"):
                    last = self._initial_messages[-1]
                    self._initial_messages[-1] = LLMMessage(
                        role="user",
                        content=(last.content or "") + "\n\n" + self._memory_context["user_addon"],
                    )


        # If trajectory recorder is set, start recording
        if self._trajectory_recorder:
            self._trajectory_recorder.start_recording(
                agent_type=self.agent_type,
                task=task,
                provider=self._llm_client.provider.value,
                model=self._model_config.model,
                max_steps=self._max_steps,
            )
            self._trajectory_recorder.start_recording_first(task=task)

    @override
    async def execute_task(self, tools=None) -> AgentExecution:
        # """Execute the task and finalize trajectory recording."""
        execution = await super().execute_task(tools)

        # When the LLM ends with a tool call (e.g. task_done), llm_response.content is
        # empty/None, leaving final_result blank.  In batch mode fall back to the git
        # diff so the trajectory report always shows something meaningful.
        # In interactive_chat mode skip this: the diff would flood the chat window and
        # the user can open the trajectory report themselves via the 'o' hotkey.
        task_kind = self._task.get("task_kind", "batch_function") if isinstance(self._task, dict) else "batch_function"
        if execution.success and not execution.final_result and task_kind != "interactive_chat":
            execution.final_result = self.get_git_diff() or "Task completed."

        # Finalize trajectory recording if recorder is available
        if self._trajectory_recorder:
            self._trajectory_recorder.finalize_recording(
                agent_type=self.agent_type,
                success=execution.success, final_result=execution.final_result
            )
            self._trajectory_recorder.finalize_recording_last(
                success=execution.success, final_result=execution.final_result
            )

        # Write Layer B episodic index + Layer C candidates after each task.
        # Interactive mode uses the same "per_task" default — each turn is one episode.
        memory_write_policy = (self._extra_args or {}).get("memory_write_policy", "per_task")
        if (self._memory_manager is not None
                and self._trajectory_recorder is not None
                and memory_write_policy == "per_task"):
            trajectory_data = self._trajectory_recorder.trajectory_data
            trajectory_path = str(self._trajectory_recorder.trajectory_path)
            self._memory_manager.write_episode(
                trajectory_data=trajectory_data,
                trajectory_path=trajectory_path,
            )
            self._memory_manager.write_candidate(
                trajectory_data=trajectory_data,
                scopes=self._memory_scopes,
            )
            self._memory_manager.consolidate(scopes=self._memory_scopes)

        if self.patch_path is not None:
            with open(self.patch_path, "w") as patch_f:
                model_patch = self.get_git_diff()
                # patch = self.remove_patches_to_tests(model_patch)
                _ = patch_f.write(model_patch)

        return execution

    def get_system_prompt(self) -> str:
        """Get the system prompt for CodeAgent (batch path only)."""
        db_key = self._task.get("database", "")
        db_display = self.db_name.get(db_key, db_key or "Unknown Database")
        system_prompt = SYSTEM_PROMPT_CODE_AGENT.format(database=db_display).strip()
        addons = self._build_system_addons()
        if addons:
            system_prompt += "\n\n" + addons
        return system_prompt

    def _build_system_addons(self) -> str:
        """Collect skills / OPENCOOK.md / memory system_addon for interactive system prompt."""
        parts = []
        if self.skill_manager:
            sec = self.skill_manager.render_prompt_section()
            if sec:
                parts.append(sec)
        opencook = self._load_opencook_instructions()
        if opencook:
            parts.append(
                f'<project_instructions source="OPENCOOK.md">\n{opencook}\n</project_instructions>'
            )
        if self._memory_context.get("system_addon"):
            parts.append(self._memory_context["system_addon"])
        return "\n\n".join(parts)

    def _load_opencook_instructions(self) -> str:
        """Load OPENCOOK.md with two-level priority:
        1. {project_path}/OPENCOOK.md  — project-specific instructions (highest priority)
        2. ~/.opencook/OPENCOOK.md     — user-global fallback
        Returns empty string when neither exists.
        """
        if hasattr(self, "_opencook_instructions_cache"):
            return self._opencook_instructions_cache
        from pathlib import Path
        candidates = [
            Path(self.project_path) / "OPENCOOK.md",
            Path("~/.opencook/OPENCOOK.md").expanduser(),
        ]
        for path in candidates:
            if path.is_file():
                self._opencook_instructions_cache = path.read_text(encoding="utf-8").strip()
                return self._opencook_instructions_cache
        self._opencook_instructions_cache = ""
        return self._opencook_instructions_cache

    def get_initial_user_prompt(self, plan=None) -> str:
        """Get the initial user prompt for CodeAgent."""
        if plan is None:
            plan = ""

        user_prompt = USER_PROMPT_CODE_AGENT.format(
            database=self.db_name[self._task["database"]], directory=self._task["directory"],
            func_name=self._task["func_name"], self_specification=self.self_specification,
            other_specification=self.other_specification, plan=plan, dependency=self.dependency,
        ).strip()

        # Append past episode summaries (Layer B) cached from new_task()
        if self._memory_context.get("user_addon"):
            user_prompt += "\n\n" + self._memory_context["user_addon"]

        return user_prompt

    def get_processed_code(self, code) -> str:
        code_str = ""
        for no, (file, content) in enumerate(code.items()):
            code_str += f"<absolute_file_path{no + 1}>\n\t{file}\n\t</absolute_file_path{no + 1}>\n"
            code_str += f"<code_content{no + 1}>\n\t{dedent(content).strip()}\n\t</code_content{no + 1}>\n"

        return code_str.strip()

    @override
    def reflect_on_result(self, tool_results: list[ToolResult]) -> str | None:
        return None

    def get_git_diff(self) -> str:
        """Get the git diff of the project."""
        if not os.path.isdir(self.project_path):
            return ""
        pwd = os.getcwd()
        os.chdir(self.project_path)
        try:
            if not self.base_commit:
                stdout = subprocess.check_output(["git", "--no-pager", "diff"]).decode()
            else:
                stdout = subprocess.check_output(
                    ["git", "--no-pager", "diff", self.base_commit, "HEAD"]
                ).decode()
        except (subprocess.CalledProcessError, FileNotFoundError):
            stdout = ""
        finally:
            os.chdir(pwd)
        return stdout

    # Copyright (c) 2024 paul-gauthier
    # SPDX-License-Identifier: Apache-2.0
    # Original remove_patches_to_tests function was released under Apache-2.0 License, with the full license text
    # available at https://github.com/Aider-AI/aider-swe-bench/blob/6e98cd6c3b2cbcba12976d6ae1b07f847480cb74/LICENSE.txt
    # Original function is at https://github.com/Aider-AI/aider-swe-bench/blob/6e98cd6c3b2cbcba12976d6ae1b07f847480cb74/tests.py#L45
    def remove_patches_to_tests(self, model_patch: str) -> str:
        """
        Remove any changes to the tests directory from the provided patch.
        This is to ensure that the model_patch does not disturb the repo's
        tests when doing acceptance testing with the `test_patch`.
        """
        lines = model_patch.splitlines(keepends=True)
        filtered_lines: list[str] = []
        test_patterns = ["/test/", "/tests/", "/testing/", "test_", "tox.ini"]
        is_tests = False

        for line in lines:
            if line.startswith("diff --git a/"):
                target_path = line.split()[-1]
                is_tests = target_path.startswith("b/") and any(
                    p in target_path for p in test_patterns
                )

            if not is_tests:
                filtered_lines.append(line)

        return "".join(filtered_lines)

    @override
    def llm_indicates_task_completed(self, llm_response: LLMResponse) -> (bool, dict):
        """Check if the LLM indicates that the task is completed."""
        if llm_response.tool_calls is None:
            return False, dict()
        return any(tool_call.name == "task_done" for tool_call in llm_response.tool_calls), dict()

    @override
    async def _is_task_completed(self, code_dict: dict, step: AgentStep) -> (bool, list[LLMMessage]):
        """Enhanced task completion detection."""
        # Interactive turns complete immediately — test_subagent runs only if the model
        # explicitly calls it, not as an automatic post-task step.
        if self._task.get("task_kind") != "batch_function":
            return True, []

        test_tool = next((t for t in self._tools if t.get_name() == "test_subagent"), None)
        if test_tool is None:
            # No test_subagent in tool list — complete directly.
            # Whether the tool is included is controlled by config, not by this method.
            return True, []

        step.state = AgentStepState.CALLING_TOOL
        self._update_cli_console(step)

        # Call test_tool directly (not via _tool_caller) because _tool_caller holds the
        # original empty _tools list from __init__; self._tools is reassigned in new_task().
        exec_result = await test_tool.execute(ToolCallArguments({}))

        # Wrap into ToolResult so the UI can display the verification outcome.
        # Use the task_done call_id so the display code (which matches by call_id against
        # step.tool_calls) can find this result and show it in the step panel.
        task_done_call = next(
            (tc for tc in (step.tool_calls or []) if tc.name == "task_done"), None
        )
        task_done_call_id = task_done_call.call_id if task_done_call else f"call_{uuid.uuid4().hex[:16]}"
        display_result = exec_result.output or exec_result.error  # show error text if no output
        step.tool_results = [ToolResult(
            name="test_subagent",
            call_id=task_done_call_id,
            success=exec_result.error_code == 0,
            result=display_result,
            error=exec_result.error,
        )]

        syntax_compliance_semantic_check = exec_result.error is None
        content = exec_result.output if syntax_compliance_semantic_check else exec_result.error
        messages = [] if syntax_compliance_semantic_check else [LLMMessage(role="user", content=content or "")]

        if syntax_compliance_semantic_check:
            self.code_format = code_dict
            self.code_completed = code_dict
            if self.must_patch == "true":
                model_patch = self.get_git_diff()
                patch = self.remove_patches_to_tests(model_patch)
                # if not patch.strip():
                #     return False, [LLMMessage(role="user", content=self.task_incomplete_message())]

        return syntax_compliance_semantic_check, messages

    @override
    def task_incomplete_message(self) -> str:
        """Return a message indicating that the task is incomplete."""
        return "ERROR! Your Patch is empty. Please provide a patch that fixes the problem."

    @override
    async def cleanup_mcp_clients(self) -> None:
        """Clean up all MCP clients to prevent async context leaks."""
        for client in self.mcp_clients:
            with contextlib.suppress(Exception):
                # Use a generic server name for cleanup since we don't track which server each client is for
                await client.cleanup("cleanup")
        self.mcp_clients.clear()
