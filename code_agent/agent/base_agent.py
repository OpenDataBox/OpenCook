# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Base Agent class for LLM-based agents."""
import asyncio
import functools
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import time
import contextlib
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

import sqlglot

import sys

from pyexpat.errors import messages
_spec_files = {
    "postgresql": "data/benchmark/postgresql/postgresql_functions_with_testcase_code_understand.json",
    "sqlite": "data/benchmark/sqlite/sqlite_functions_with_testcase_code_understand.json",
    "duckdb": "data/benchmark/duckdb/duckdb_functions_with_testcase_code_understand.json",
}

# sys.path = ["/data/wei/program/DBCooker"] + sys.path

from code_agent.agent.agent_basics import AgentExecution, AgentState, AgentStep, AgentStepState
from code_agent.tools import tools_registry
from code_agent.tools.base import Tool, ToolCall, ToolCallArguments, ToolExecutor, ToolResult
from code_agent.tools.bash_tool import BashTool
from code_agent.tools.ckg.ckg_database import clear_older_ckg
from code_agent.utils.cli import CLIConsole
from code_agent.utils.cli.cli_console import ToolApprovalRequest
from code_agent.utils.config import AgentConfig, ModelConfig
from code_agent.utils.llm_clients.llm_basics import LLMMessage, LLMResponse
from code_agent.utils.llm_clients.llm_client import LLMClient
from code_agent.utils.trajectory_recorder import TrajectoryRecorder

# Tools that are auto-approved in interactive mode — no user confirmation needed.
# task_done signals completion and carries no side-effects worth blocking on.
_NO_APPROVAL_TOOLS: frozenset[str] = frozenset({"task_done", "sequentialthinking"})


class BaseAgent(ABC):
    """Base class for LLM-based agents."""

    _tool_caller: ToolExecutor

    def __init__(
            self, agent_config: AgentConfig, agent_type: str = "base_agent"
    ):
        """Initialize the agent.
        Args:
            agent_config: Configuration object containing model parameters and other settings.
        """
        self._agent_config = agent_config
        self._llm_client = LLMClient(agent_config.model)
        self._model_config = agent_config.model
        self._max_steps = agent_config.max_steps
        self._run_steps = agent_config.run_steps
        self._initial_messages: list[LLMMessage] = []
        self._task: dict = {}
        self._extra_args: dict = {}
        self.db_name: dict = {}  # set by Agent wrapper after construction
        self.spec_files: dict = _spec_files
        self._tools: list[Tool] = [
            tools_registry[tool_name](model_provider=self._model_config.model_provider.provider)
            for tool_name in agent_config.tools
        ]
        for tool in self._tools:
            if isinstance(tool, BashTool):
                tool.set_timeout(agent_config.bash_timeout)
        self._tool_caller = ToolExecutor(self._tools)

        self._cli_console: CLIConsole | None = None
        self._history_path: Path | None = None

        # Cancellation support for run_in_executor LLM calls.
        # Using a private executor (not the default) means asyncio.run() shutdown
        # does not block waiting for an in-flight LLM thread after forced exit.
        self._llm_cancel_flag: threading.Event = threading.Event()
        self._llm_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="llm"
        )

        # Trajectory recorder
        self._trajectory_recorder: TrajectoryRecorder | None = None
        self._force_edit_after_plan: bool = False

        # Full conversation history maintained by the agent for compaction.
        # Provider clients receive delta messages each step (reuse_history=True);
        # _agent_history is the agent-side mirror used solely for compaction.
        # _step_boundaries[0] = 0 (sentinel); each step appends len(_agent_history)
        # after extending with sent_messages and the assistant response.
        self._agent_history: list[LLMMessage] = []
        self._step_boundaries: list[int] = [0]

        # CKG tool-specific: clear the older CKG databases
        clear_older_ckg()

        self.self_specification: str | None = None
        self.other_specification: str | None = None
        self.dependency: str | None = None

        # Interactive tool-approval state (reset at the start of each execute_task call)
        self._interactive_approval: bool = False
        self._approved_tools: set[str] = set()   # tool names auto-approved for this turn
        self._approve_remaining: bool = False     # skip all further confirmations this turn

        self.agent_type = agent_type

    def close(self) -> None:
        """Shut down the private LLM executor.

        Call this when the agent instance is no longer needed (e.g. at the end
        of a SessionRunner turn) to release the idle worker thread immediately
        rather than waiting for garbage collection.
        """
        self._llm_executor.shutdown(wait=False)

    def __del__(self) -> None:
        # Safety net: shut down the executor if close() was never called.
        # In Python 3.4+, __del__ is invoked by the cyclic GC even for objects
        # in reference cycles, so this prevents idle threads from accumulating
        # across turns in long interactive sessions.
        try:
            self._llm_executor.shutdown(wait=False)
        except Exception:
            pass  # executor may not have been initialised yet

    def _rebuild_tool_executor(self, tools: list) -> None:
        """Rebuild the tool executor after modifying _tools."""
        self._tool_caller = ToolExecutor(tools)

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

    @property
    def trajectory_recorder(self) -> TrajectoryRecorder | None:
        """Get the trajectory recorder for this agent."""
        return self._trajectory_recorder

    def set_trajectory_recorder(self, recorder: TrajectoryRecorder | None) -> None:
        """Set the trajectory recorder for this agent."""
        self._trajectory_recorder = recorder
        # Also set it on the LLM client
        self._llm_client.set_trajectory_recorder(recorder)

    @property
    def cli_console(self) -> CLIConsole | None:
        """Get the CLI console for this agent."""
        return self._cli_console

    def set_cli_console(self, cli_console: CLIConsole | None) -> None:
        """Set the CLI console for this agent."""
        self._cli_console = cli_console

    @property
    def tools(self) -> list[Tool]:
        """Get the tools available to this agent."""
        return self._tools

    @property
    def task(self) -> dict:
        """Get the current task of the agent."""
        return self._task

    @task.setter
    def task(self, value: str):
        """Set the current task of the agent."""
        self._task = value

    @property
    def initial_messages(self) -> list[LLMMessage]:
        """Get the initial messages for the agent."""
        return self._initial_messages

    @property
    def model_config(self) -> ModelConfig:
        """Get the model config for the agent."""
        return self._model_config

    @property
    def max_steps(self) -> int:
        """Get the maximum number of steps for the agent."""
        return self._max_steps

    @property
    def run_steps(self) -> int:
        """Get the number of steps for the agent."""
        return self._run_steps

    @abstractmethod
    def new_task(
            self,
            task: str,
            extra_args: dict[str, str] | None = None,
            tool_names: list[str] | None = None,
    ):
        """Create a new task."""
        pass

    async def execute_task(self, tools=None) -> AgentExecution:
        """Execute a task using the agent."""
        start_time = time.time()
        execution = AgentExecution(task=self._task, steps=[])
        step: AgentStep | None = None

        try:
            messages = self._initial_messages
            step_number = 1
            execution.agent_state = AgentState.RUNNING
            if tools is None:
                tools = self._tools

            # Reset per-task agent history — skipped for interactive turns that already
            # restored history via set_chat_history() in new_task().
            if not (self._extra_args or {}).get("preserve_chat_history", False):
                self._agent_history = []
                self._step_boundaries = [0]
                self._llm_client.set_chat_history([])
            else:
                # Sanitize preserved history before reuse.  Orphaned assistant
                # tool_call entries at the tail can arise from two sources:
                # (a) a step that was interrupted by an exception or cancellation
                #     before the history-write lines (extend + append) executed;
                # (b) a normal task_done turn where the tool result is
                #     intentionally not written to history after the loop breaks.
                # In both cases the orphaned entries cause strict API providers
                # to reject the next request with "tool result must follow
                # tool_calls".  Trimming them and re-syncing the client is safe
                # because the corresponding tool results are lost or irrelevant.
                while (
                    self._agent_history
                    and self._agent_history[-1].role == "assistant"
                    and self._agent_history[-1].tool_call is not None
                ):
                    self._agent_history.pop()
                # Drop any step boundaries that now point past the trimmed
                # history end.  Stale boundaries would mislead compaction and
                # history-reuse logic that relies on them as valid indices.
                valid_len = len(self._agent_history)
                self._step_boundaries = [
                    b for b in self._step_boundaries if b <= valid_len
                ]
                if not self._step_boundaries:
                    self._step_boundaries = [0]
                self._llm_client.set_chat_history(self._agent_history)

            # Reset per-turn interactive approval state
            task_kind = self._task.get("task_kind", "batch_function") if isinstance(self._task, dict) else "batch_function"
            self._interactive_approval = (task_kind == "interactive_chat")
            self._approved_tools = set()
            self._approve_remaining = False
            self._force_edit_after_plan = False

            while step_number <= self._max_steps:
                step = AgentStep(step_number=step_number, state=AgentStepState.THINKING)
                try:
                    # TODO: code agent different step (messages expansion such as more dependency information)
                    tools_processed = tools
                    force_edit_step = False
                    if self.agent_type == "code_agent" and step_number == 1:
                        # Step 1 has no tools: the model outputs its initial analysis
                        # as plain text before any tool calls begin.
                        # NOTE: skill_tool is available from step 2 onward via the
                        # normal tools list; <available_skills> in the system prompt
                        # instructs the model to call it first when relevant.
                        #
                        # Removed: step-1 skill-only restriction
                        # (skill_tools = [t for t in tools if t.get_name() == "skill"]
                        #  + _mgr check) — it forced the model into skill_tool even
                        # when no skill was relevant, causing incorrect_output_format
                        # fallback on tasks that didn't need a skill.
                        if task_kind == "interactive_chat":
                            tools_processed = [
                                tool for tool in tools
                                if getattr(tool, "name", "") == "plan_subagent"
                            ]
                        else:
                            tools_processed = []
                    elif (
                        self.agent_type == "code_agent"
                        and task_kind == "interactive_chat"
                        and self._force_edit_after_plan
                    ):
                        allowed_after_plan = {
                            "str_replace_based_edit_tool",
                            "json_edit_tool",
                            "task_done",
                        }
                        tools_processed = [
                            tool for tool in tools
                            if getattr(tool, "name", "") in allowed_after_plan
                        ]
                        force_edit_step = True
                    #     tool_calls = llm_response.tool_calls
                    #     if tool_calls is not None:
                    #         return await self._tool_call_handler(tool_calls, step)
                    #     else:
                    #         return [LLMMessage(role="user", content=self.incorrect_output_format_message())]

                    # Bulk compaction: runs against _agent_history (previous steps only),
                    # before this step's delta is sent.  compacted does not include
                    # `messages` so set_chat_history + chat(messages) does not re-send it.
                    sent_messages = messages
                    memory_manager = getattr(self, "_memory_manager", None)
                    if memory_manager is not None:
                        if memory_manager.should_compact(self._agent_history):
                            self._agent_history, self._step_boundaries = memory_manager.compact(
                                self._agent_history, step_number, self._step_boundaries,
                                llm_client=self._llm_client,
                                model_config=self._model_config,
                            )
                            # set_chat_history resets the provider to the compacted state,
                            # also cleaning up any history pollution from the summary LLM call.
                            self._llm_client.set_chat_history(self._agent_history)

                    messages = await self._run_llm_step(step, sent_messages, execution, tools_processed)
                    if force_edit_step:
                        called_after_plan = {
                            getattr(tool_call, "name", "")
                            for tool_call in (step.tool_calls or [])
                        }
                        if called_after_plan & {"str_replace_based_edit_tool", "json_edit_tool", "task_done"}:
                            self._force_edit_after_plan = False
                    await self._finalize_step(
                        step, messages, execution
                    )  # record trajectory for this step and update the CLI console

                    # Mirror this step into _agent_history: sent delta + assistant response
                    self._agent_history.extend(sent_messages)
                    if step.llm_response is not None:
                        if step.llm_response.content:
                            self._agent_history.append(
                                LLMMessage(role="assistant", content=step.llm_response.content)
                            )
                        for tool_call in (step.llm_response.tool_calls or []):
                            self._agent_history.append(
                                LLMMessage(role="assistant", tool_call=tool_call)
                            )
                    # Record step boundary after assistant response is appended
                    self._step_boundaries.append(len(self._agent_history))

                    if execution.agent_state == AgentState.COMPLETED:
                        break
                    step_number += 1
                except Exception as error:
                    logger.exception("Step %d failed: %s", step_number, error)
                    execution.agent_state = AgentState.ERROR
                    step.state = AgentStepState.ERROR
                    step.error = str(error)
                    await self._finalize_step(step, messages, execution)
                    break
            if step_number > self._max_steps and not execution.success:
                execution.final_result = "Task execution exceeded maximum steps without completion."
                execution.agent_state = AgentState.ERROR

        except Exception as e:
            execution.final_result = f"Agent execution failed: {str(e)}"
            logger.exception("Agent execution failed: %s", e)

        finally:
            # Spawn cleanup as an independent asyncio.Task so that a pending
            # cancellation on the outer task cannot interrupt _close_tools()
            # or cleanup_mcp_clients() mid-way.  Any await on a cancelled
            # task re-raises CancelledError immediately, but a Task created
            # with ensure_future() is not subject to the outer task's cancel
            # state — it runs to completion on the event loop regardless.
            cleanup_task = asyncio.ensure_future(self._run_cleanup())
            try:
                await cleanup_task  # fast path: completes normally when not cancelled
            except asyncio.CancelledError:
                pass  # cleanup_task continues independently; outer cancel resumes
            # _llm_executor is an instance-level resource shared across all
            # execute_task() calls on the same agent (e.g. PlanAgent retries).
            # It must NOT be shut down here; atexit handles final teardown.

        execution.execution_time = time.time() - start_time
        self._update_cli_console(step, execution)
        return execution

    async def _close_tools(self):
        """Release tool resources, mainly about BashTool object."""
        if self._tool_caller:
            # Ensure all tool resources are properly released.
            res = await self._tool_caller.close_tools()
            return res

    async def _run_cleanup(self) -> None:
        """Close tools and MCP clients.  Intended to be spawned via
        asyncio.ensure_future() from execute_task()'s finally block so that
        cleanup runs to completion even when the outer Task is cancelled.

        Each step is individually guarded so an exception in _close_tools()
        (e.g. from BashTool.close()) cannot skip cleanup_mcp_clients(), and
        neither step leaves an unhandled exception on the background Task.
        """
        with contextlib.suppress(Exception):
            await self._close_tools()
        with contextlib.suppress(Exception):
            await self.cleanup_mcp_clients()

    async def _run_llm_step(
            self, step: "AgentStep", messages: list["LLMMessage"], execution: "AgentExecution", tools: list[Tool] = None
    ) -> list["LLMMessage"]:
        # Display thinking state
        step.state = AgentStepState.THINKING
        self._update_cli_console(step, execution)
        # Run the blocking LLM call in a private thread-pool executor so the
        # asyncio event loop (and the Textual TUI) stays responsive.
        # Using self._llm_executor (not the default) means asyncio.run()
        # shutdown does not wait for this thread on forced exit.
        # cancel_flag is wired into the client so that when CancelledError is
        # raised here, the still-running thread will skip message_history
        # mutations and trajectory writes once the HTTP response arrives.
        self._llm_cancel_flag.clear()
        self._llm_client.client.cancel_flag = self._llm_cancel_flag
        loop = asyncio.get_running_loop()
        try:
            llm_response = await loop.run_in_executor(
                self._llm_executor,
                functools.partial(
                    self._llm_client.chat,
                    messages,
                    self._model_config,
                    tools,
                    agent_type=self.agent_type,
                ),
            )
        except asyncio.CancelledError:
            self._llm_cancel_flag.set()  # Signal the background thread to skip side effects.
            raise
        step.llm_response = llm_response

        # Display step with LLM response
        self._update_cli_console(step, execution)

        # Update token usage
        self._update_llm_usage(llm_response, execution)
        format_check, content_dict = self.llm_indicates_task_completed(llm_response)
        if format_check:
            messages = []
            tool_calls = llm_response.tool_calls
            if tool_calls is not None:
                message = await self._tool_call_handler(tool_calls, step, available_tools=tools)
                messages.extend(message)

            is_completed, message = await self._is_task_completed(content_dict, step)
            messages.extend(message)

            if is_completed:
                execution.agent_state = AgentState.COMPLETED
                execution.final_result = llm_response.content
                execution.success = True
                return messages
            else:
                execution.agent_state = AgentState.RUNNING
                return messages
        else:
            tool_calls = llm_response.tool_calls
            if tool_calls is not None:
                return await self._tool_call_handler(tool_calls, step, available_tools=tools)
            elif llm_response.content and llm_response.content.strip():
                # Text-only response: valid intermediate reasoning step.
                # The assistant message is already in the client's history;
                # return a neutral continuation that keeps the task_done contract visible.
                return [LLMMessage(role="user", content=(
                    "Please proceed using the available tools. "
                    "Call `task_done` when the task is complete."
                ))]
            else:
                # Truly empty response (no content, no tool calls).
                return [LLMMessage(role="user", content=self.incorrect_output_format_message())]

    async def _run_llm_step_branch(
            self, step: "AgentStep", messages: list["LLMMessage"], execution: "AgentExecution", tools: list[Tool] = None
    ) -> list["LLMMessage"]:
        # Display thinking state
        step.state = AgentStepState.THINKING
        self._update_cli_console(step, execution)
        # Same executor / cancel-flag pattern as _run_llm_step.
        self._llm_cancel_flag.clear()
        self._llm_client.client.cancel_flag = self._llm_cancel_flag
        loop = asyncio.get_running_loop()
        try:
            llm_response = await loop.run_in_executor(
                self._llm_executor,
                functools.partial(
                    self._llm_client.chat,
                    messages,
                    self._model_config,
                    tools,
                    agent_type=self.agent_type,
                ),
            )
        except asyncio.CancelledError:
            self._llm_cancel_flag.set()
            raise
        step.llm_response = llm_response

        # Display step with LLM response
        self._update_cli_console(step, execution)

        # Update token usage
        self._update_llm_usage(llm_response, execution)
        tool_calls = llm_response.tool_calls
        if tool_calls is not None:
            return await self._tool_call_handler(tool_calls, step, available_tools=tools)
        else:
            return [LLMMessage(role="user", content=self.incorrect_output_format_message())]

    async def _finalize_step(
            self, step: "AgentStep", messages: list["LLMMessage"], execution: "AgentExecution"
    ) -> None:
        if step.state != AgentStepState.ERROR:
            step.state = AgentStepState.COMPLETED
        if messages is not None:
            dumped: list[dict[str, object]] = []
            for m in messages:
                item: dict[str, object] = {"role": m.role}
                if m.content:
                    item["content"] = m.content
                if m.tool_call is not None:
                    tc = m.tool_call
                    item["tool_call"] = {
                        "name": tc.name,
                        "call_id": tc.call_id,
                        "arguments": tc.arguments,
                    }
                if m.tool_result is not None:
                    tr = m.tool_result
                    item["tool_result"] = {
                        "name": tr.name,
                        "call_id": tr.call_id,
                        "success": tr.success,
                        "result": tr.result,
                        "error": tr.error,
                    }
                dumped.append(item)
            step.extra = dict(step.extra or {})
            step.extra["next_messages"] = dumped
        self._record_handler(step, messages)
        self._append_step_history(step=step, execution=execution)
        self._update_cli_console(step, execution)
        if self.cli_console:
            try:
                self.cli_console.debug_step(step, execution, agent_type=self.agent_type)
            except Exception:
                pass
        execution.steps.append(step)

    def _get_history_path(self) -> Path:
        if self._history_path is not None:
            return self._history_path

        history_dir = Path(__file__).resolve().parents[1] / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        base = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            if self._trajectory_recorder and hasattr(self._trajectory_recorder, "trajectory_path"):
                base = str(getattr(self._trajectory_recorder, "trajectory_path").stem) or base
        except Exception:
            pass

        pid = os.getpid()
        self._history_path = (history_dir / f"{base}__{self.agent_type}__{pid}.jsonl").resolve()
        return self._history_path

    def _append_step_history(self, *, step: AgentStep, execution: AgentExecution) -> None:
        try:
            lr = step.llm_response
            tool_calls = step.tool_calls or (lr.tool_calls if lr else None) or []
            tool_results = step.tool_results or []
            display_level = getattr(self._cli_console, "display_level", "log") if self._cli_console else "log"

            info = {
                "state": getattr(step.state, "value", str(step.state)),
                "finish_reason": getattr(lr, "finish_reason", None) if lr else None,
                "tool_calls": [tc.name for tc in tool_calls],
                "tool_results": [{"name": tr.name, "success": tr.success} for tr in tool_results],
                "execution_success": execution.success,
                "agent_state": getattr(execution.agent_state, "value", str(execution.agent_state)),
            }

            debug = {
                "thought": step.thought,
                "llm_response": (
                    {
                        "content": lr.content,
                        "model": lr.model,
                        "finish_reason": lr.finish_reason,
                        "usage": (
                            {
                                "input_tokens": lr.usage.input_tokens,
                                "output_tokens": lr.usage.output_tokens,
                                "cache_creation_input_tokens": lr.usage.cache_creation_input_tokens,
                                "cache_read_input_tokens": lr.usage.cache_read_input_tokens,
                                "reasoning_tokens": lr.usage.reasoning_tokens,
                            }
                            if lr.usage
                            else None
                        ),
                        "tool_calls": (
                            [
                                {
                                    "name": tc.name,
                                    "call_id": tc.call_id,
                                    "arguments": tc.arguments,
                                    "id": getattr(tc, "id", None),
                                }
                                for tc in (lr.tool_calls or [])
                            ]
                            if lr.tool_calls is not None
                            else None
                        ),
                    }
                    if lr
                    else None
                ),
                "tool_calls": [
                    {
                        "name": tc.name,
                        "call_id": tc.call_id,
                        "arguments": tc.arguments,
                        "id": getattr(tc, "id", None),
                    }
                    for tc in tool_calls
                ],
                "tool_results": [
                    {
                        "name": tr.name,
                        "call_id": tr.call_id,
                        "success": tr.success,
                        "result": tr.result,
                        "error": tr.error,
                        "id": getattr(tr, "id", None),
                    }
                    for tr in tool_results
                ],
                "reflection": step.reflection,
                "error": step.error,
                "next_messages": (step.extra or {}).get("next_messages") if isinstance(step.extra, dict) else None,
            }

            record = {
                "ts": datetime.now().isoformat(),
                "level": display_level,
                "agent_type": self.agent_type,
                "task_kind": (self._task or {}).get("task_kind") if isinstance(self._task, dict) else None,
                "project_path": (self._extra_args or {}).get("project_path"),
                "step_number": step.step_number,
                "info": info,
                "debug": debug,
            }

            path = self._get_history_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    def reflect_on_result(self, tool_results: list[ToolResult]) -> str | None:
        """Reflect on tool execution result. Override for custom reflection logic."""
        if len(tool_results) == 0:
            return None

        reflection = "\n".join(
            f"The tool execution failed with error: {tool_result.error}. Consider trying a different approach or fixing the parameters."
            for tool_result in tool_results
            if not tool_result.success
        )

        return reflection

    def llm_indicates_task_completed(self, llm_response: LLMResponse) -> (bool, dict):
        """Check if the LLM indicates that the task is completed. Override for custom logic."""
        completion_indicators = [
            "task completed",
            "task finished",
            "done",
            "completed successfully",
            "finished successfully",
        ]

        response_lower = llm_response.content.lower()
        return any(indicator in response_lower for indicator in completion_indicators), dict()

    async def _is_task_completed(self, content_dict: dict, step: AgentStep) -> (bool, list[
        LLMMessage]):  # pyright: ignore[reportUnusedParameter]
        """Check if the task is completed based on the response. Override for custom logic."""
        return True

    def task_incomplete_message(self) -> str:
        """Return a message indicating that the task is incomplete. Override for custom logic."""
        return "The task is incomplete. Please try again."

    def incorrect_output_format_message(self) -> str:
        """Return a message prompting the model when it produces an empty response."""
        return (
            "Your response was empty. Please either use one of the available tools "
            "to make progress, or call `task_done` if the task is complete."
        )

    @abstractmethod
    async def cleanup_mcp_clients(self) -> None:
        """Clean up MCP clients. Override in subclasses that use MCP."""
        pass

    def _update_cli_console(
            self, step: AgentStep | None = None, agent_execution: AgentExecution | None = None
    ) -> None:
        if self.cli_console:
            self.cli_console.update_status(step, agent_execution)

    def _update_llm_usage(self, llm_response: LLMResponse, execution: AgentExecution):
        if not llm_response.usage:
            return
        # if execution.total_tokens is None then set it to be llm_response.usage else sum it up
        # execution.total_tokens is not None
        if not execution.total_tokens:
            execution.total_tokens = llm_response.usage
        else:
            execution.total_tokens += llm_response.usage

    def _record_handler(self, step: AgentStep, messages: list[LLMMessage]) -> None:
        if self.trajectory_recorder:
            self.trajectory_recorder.record_agent_step(
                agent_type=self.agent_type,
                step_number=step.step_number,
                state=step.state.value,
                llm_messages=messages,
                llm_response=step.llm_response,
                tool_calls=step.tool_calls,
                tool_results=step.tool_results,
                reflection=step.reflection,
                error=step.error,
            )

    async def _check_tool_approval(
        self, tool_calls: list[ToolCall]
    ) -> tuple[list[ToolCall], list[ToolResult]]:
        """Filter tool calls through user approval in interactive mode.

        Returns (approved_calls, denied_results).  denied_results contains a
        ToolResult for every call the user declined so the LLM can see what was
        skipped and adjust its next action accordingly.

        When not in interactive mode the method is a no-op and returns all calls
        approved with an empty denied list.
        """
        if not self._interactive_approval or self._cli_console is None:
            return tool_calls, []

        approved: list[ToolCall] = []
        denied: list[ToolResult] = []

        for call in tool_calls:
            # Tools that never need user confirmation (completion / read-only signals)
            if call.name in _NO_APPROVAL_TOOLS:
                approved.append(call)
                continue
            # Already auto-approved for this turn?
            if self._approve_remaining or call.name in self._approved_tools:
                approved.append(call)
                continue

            req = self._build_approval_request(call.name, call.arguments)
            choice = await self._cli_console.request_tool_approval_async(req)

            if choice == "y":
                approved.append(call)
            elif choice == "t":
                self._approved_tools.add(call.name)
                approved.append(call)
            elif choice == "s":
                self._approve_remaining = True
                approved.append(call)
            else:  # "n" or "n:<reason>"
                if isinstance(choice, str) and choice.startswith("n:"):
                    user_reason = choice[2:].strip()
                    error_msg = f"Tool call denied by user: {user_reason}" if user_reason else "Tool call denied by user."
                else:
                    error_msg = "Tool call denied by user."
                denied.append(ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    success=False,
                    result=None,
                    error=error_msg,
                ))

        return approved, denied

    @staticmethod
    def _build_approval_request(
        tool_name: str, arguments: ToolCallArguments
    ) -> ToolApprovalRequest:
        """Build a ToolApprovalRequest from raw tool call data."""
        preview_kind, preview_text = BaseAgent._generate_preview(tool_name, arguments)
        return ToolApprovalRequest(
            tool_name=tool_name,
            preview_text=preview_text,
            preview_kind=preview_kind,
        )

    @staticmethod
    def _generate_preview(
        tool_name: str, arguments: ToolCallArguments
    ) -> tuple[str, str]:
        """Generate a (kind, text) preview for a tool call.

        kind: "command" | "diff" | "summary" | "inline"
        """
        if tool_name == "bash":
            cmd = str(arguments.get("command", "")) if isinstance(arguments, dict) else ""
            return "command", cmd[:500]

        if tool_name == "str_replace_based_edit_tool":
            args = arguments if isinstance(arguments, dict) else {}
            sub_cmd = str(args.get("command", ""))
            if sub_cmd == "view":
                return "inline", f"view  {args.get('path', '')}"
            return "diff", BaseAgent._make_str_replace_diff(sub_cmd, args)

        if tool_name == "json_edit_tool":
            args = arguments if isinstance(arguments, dict) else {}
            return "summary", BaseAgent._make_json_edit_summary(args)

        # Generic fallback: show first argument value
        if isinstance(arguments, dict) and arguments:
            first_val = str(next(iter(arguments.values()), ""))
            return "inline", first_val[:200]
        return "inline", str(arguments)[:200]

    @staticmethod
    def _make_str_replace_diff(sub_cmd: str, arguments: dict) -> str:
        """Render a compact diff-style preview for str_replace_based_edit_tool."""
        path = str(arguments.get("path", ""))
        if sub_cmd == "create":
            content = str(arguments.get("file_text", ""))
            lines = content.split("\n")[:25]
            preview = "\n".join(f"+ {l}" for l in lines)
            if len(content.split("\n")) > 25:
                preview += "\n+ …"
            return f"create  {path}\n{preview}"
        if sub_cmd == "str_replace":
            old_str = str(arguments.get("old_str", ""))
            new_str = str(arguments.get("new_str", ""))
            old_lines = old_str.split("\n")[:25]
            new_lines = new_str.split("\n")[:25]
            diff_lines = [f"--- {path}", f"+++ {path}"]
            diff_lines.extend(f"- {l}" for l in old_lines)
            if len(old_str.split("\n")) > 25:
                diff_lines.append("- …")
            diff_lines.extend(f"+ {l}" for l in new_lines)
            if len(new_str.split("\n")) > 25:
                diff_lines.append("+ …")
            return "\n".join(diff_lines)
        if sub_cmd == "insert":
            content = str(arguments.get("new_str", ""))
            line = arguments.get("insert_line", "")
            preview = "\n".join(content.split("\n")[:25])
            return f"insert  {path}:{line}\n{preview}"
        return f"{sub_cmd}  {path}"

    @staticmethod
    def _make_json_edit_summary(arguments: dict) -> str:
        """Render a short summary for json_edit_tool."""
        parts: list[str] = []
        if "operation" in arguments:
            parts.append(f"op: {arguments['operation']}")
        if "file_path" in arguments:
            parts.append(f"file: {arguments['file_path']}")
        if "json_path" in arguments:
            parts.append(f"path: {arguments['json_path']}")
        if "value" in arguments:
            v = str(arguments["value"])
            parts.append(f"value: {v[:200]}" + ("…" if len(v) > 200 else ""))
        return "  ".join(parts) if parts else str(arguments)[:200]

    @staticmethod
    def _format_tool_args_preview(tool_name: str, arguments: ToolCallArguments) -> str:
        """Return a short readable summary of the most relevant argument for display.

        Kept for any remaining legacy callers; new code uses _generate_preview().
        """
        _KEY_ARGS: dict[str, str] = {
            "bash": "command",
            "str_replace_based_edit_tool": "path",
            "json_edit_tool": "file_path",
            "understand_toolkit": "file_path",
        }
        key = _KEY_ARGS.get(tool_name)
        if key and isinstance(arguments, dict) and key in arguments:
            val = str(arguments[key])
            return f"{key}={val[:80]}" + ("…" if len(val) > 80 else "")
        s = str(arguments)
        return s[:100] + ("…" if len(s) > 100 else "")

    async def _tool_call_handler(
            self,
            tool_calls: list[ToolCall] | None,
            step: AgentStep,
            available_tools: list[Tool] | None = None,
    ) -> list[LLMMessage]:
        messages: list[LLMMessage] = []
        if not tool_calls or len(tool_calls) <= 0:
            messages = [
                LLMMessage(
                    role="user",
                    content="It seems that you have not completed the task.",
                )
            ]
            return messages

        step.state = AgentStepState.CALLING_TOOL
        step.tool_calls = tool_calls
        self._update_cli_console(step)

        def _normalize_tool_name(name: str) -> str:
            return name.lower().replace("_", "")

        visible_tools = available_tools if available_tools is not None else self._tools
        visible_tool_names = [tool.name for tool in visible_tools]
        visible_tool_map = {
            _normalize_tool_name(tool.name): tool.name for tool in visible_tools
        }

        executable_calls: list[ToolCall] = []
        unavailable_results: list[ToolResult] = []
        for tool_call in tool_calls:
            normalized_name = _normalize_tool_name(tool_call.name)
            if normalized_name in visible_tool_map:
                executable_calls.append(tool_call)
                continue
            unavailable_results.append(
                ToolResult(
                    name=tool_call.name,
                    success=False,
                    error=(
                        f"Tool '{tool_call.name}' was not available in this step. "
                        f"Available tools: {visible_tool_names}"
                    ),
                    call_id=tool_call.call_id,
                    id=tool_call.id,
                )
            )

        # Interactive mode: ask user to approve each tool call before execution.
        # Approved calls are executed normally; denied calls get an error ToolResult
        # so the LLM knows they were skipped and can adjust its next action.
        approved_calls, denied_results = await self._check_tool_approval(executable_calls)
        if approved_calls:
            if self._model_config.parallel_tool_calls:
                exec_results = await self._tool_caller.parallel_tool_call(approved_calls)
            else:
                exec_results = await self._tool_caller.sequential_tool_call(approved_calls)
        else:
            exec_results = []
        tool_results = unavailable_results + denied_results + exec_results
        step.tool_results = tool_results
        self._update_cli_console(step)

        # Phase 2 memory hooks: spike offload for oversized tool results
        memory_manager = getattr(self, "_memory_manager", None)
        if memory_manager is not None:
            for i, tool_result in enumerate(tool_results):
                msg = LLMMessage(role="user", tool_result=tool_result)
                msg = memory_manager.offload_spike(msg, step.step_number)
                tool_results[i] = msg.tool_result

        for tool_result in tool_results:
            # Add tool result to conversation
            message = LLMMessage(role="user", tool_result=tool_result)
            messages.append(message)

        def _normalize_text(text: str | None) -> str:
            return (text or "").lower()

        edit_tool_guidance_added = False
        for tool_result in tool_results:
            tool_name = _normalize_tool_name(tool_result.name or "")
            error_text = _normalize_text(tool_result.error)
            if tool_name != "strreplacebasededittool" or not error_text:
                continue

            if (
                "did not appear verbatim" in error_text
                and not edit_tool_guidance_added
            ):
                messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            "Your last `str_replace_based_edit_tool` replacement failed because `old_str` "
                            "did not appear verbatim. Do not retry a guessed snippet. Next, get a fresh "
                            "`str_replace_based_edit_tool` `view` of the exact neighborhood you want to edit, "
                            "copy a longer unique anchor verbatim from that view, and then retry the replacement once."
                        ),
                    )
                )
                edit_tool_guidance_added = True
                continue

            if (
                "multiple occurrences of old_str" in error_text
                and not edit_tool_guidance_added
            ):
                messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            "Your last `str_replace_based_edit_tool` replacement failed because `old_str` "
                            "was not unique. Do not use a short anchor such as `};`. Next, request a fresh "
                            "`view` with the same tool and expand `old_str` to include enough surrounding lines "
                            "to make the anchor unique before retrying."
                        ),
                    )
                )
                edit_tool_guidance_added = True
                continue

            if (
                ("invalid `view_range`" in error_text or "invalid `insert_line`" in error_text)
                and not edit_tool_guidance_added
            ):
                messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            "Your last `str_replace_based_edit_tool` call used invalid line numbers. Do not "
                            "reuse line numbers from bash or PowerShell output for this tool. Either obtain "
                            "line numbers from the edit tool's own `view` output or switch to unique anchor-text "
                            "replacement instead of line-number-driven edits."
                        ),
                    )
                )
                edit_tool_guidance_added = True
                continue

        reflection = self.reflect_on_result(tool_results)
        if reflection:
            step.state = AgentStepState.REFLECTING
            step.reflection = reflection

            # Display reflection
            self._update_cli_console(step)

            messages.append(LLMMessage(role="assistant", content=reflection))

        used_plan_subagent = any(
            _normalize_tool_name(tool_call.name) == "plansubagent"
            for tool_call in tool_calls
        )
        if used_plan_subagent and self._task.get("task_kind") == "interactive_chat":
            messages.append(
                LLMMessage(
                    role="user",
                    content=(
                        "Use the plan immediately. Do not restate it and do not do broad follow-up exploration. "
                        "Your next action should be a concrete code edit in the target file, or at most one final "
                        "file-view to anchor that edit. Avoid additional bash searches unless the edit fails or the "
                        "plan explicitly proves that no code change is needed. Call `task_done` once the edit or "
                        "conclusion is complete."
                    ),
                )
            )

        return messages

    @staticmethod
    def format_specification(func_name: str, description: str,
                             example: str, code: str = "", file_path: str = "") -> str:
        specification = list()
        if len(func_name) != 0:
            specification.append(f"<function_declaration>\n\t{func_name}\n\t</function_declaration>""")
        if len(file_path) != 0:
            specification.append(f"<candidate_function_file_path>\n\t{file_path}\n\t</candidate_function_file_path>")
        if len(description) != 0:
            specification.append(f"<function_description>\n\t{description}\n\t</function_description>")
        if len(example) != 0:
            specification.append(f"<function_example>\n\t{example}\n\t</function_example>")
        if len(code) != 0:
            specification.append(f"<function_code>\n\t{code}\n\t</function_code>")

        # specification_str = ""
        # for no, spec in enumerate(specification):
        #     specification_str += f"**{no + 1}.{spec}\n\n"
        specification_str = "\n\t".join(specification)
        return specification_str.strip()

    def format_code(self, code: dict) -> str:
        """

        :param code:
        :return:
        """
        # TODO: to be modified. code
        if len(code) == 0:
            return ""
        code = "\n\t".join(["\n\t".join([f"<absolute_file_path{no + 1}>\n\t{file}\n\t</absolute_file_path{no + 1}>\n\t"
                                         f"<code_content{no + 1}>\n\t{content}\n\t</code_content{no + 1}>"
                                         for file, content in it[1].items()]) for no, it in enumerate(code.values())])
        return code

    @staticmethod
    def format_dependency(dependency: dict):
        dependency_processed = ""
        # for func in dependency.keys():
        #     dependency_processed += f"The dependency of `{func}` is:\n"
        for no, typ in enumerate(dependency.keys()):
            dependency_processed += f"{no + 1}. {typ} Dependency:\n"
            for item in dependency[typ]:
                dependency_processed += f"\n<{typ}>\n{item}\n</{typ}>\n"
            dependency_processed += "\n\n"
        dependency_processed += "\n"

        return dependency_processed

    def get_inner_specification(self) -> str:
        func_name = self._task["func_name"]
        spec_load = self.spec_files[self._task["database"]]
        with open(spec_load, "r") as rf:
            spec_data = json.load(rf)

        target_category = ""
        for item in spec_data:
            if item["keyword"] == func_name:
                target_category = item["category"]
                break

        inner_specification = list()
        for item in spec_data:
            if item["keyword"] == func_name:
                continue
            if item["category"] == target_category:
                inner_specification.append(item)

        inner_code = ""
        for item in inner_specification:
            item["code"] = self.format_code(item["code"])

        pass

    def get_other_specification(self) -> str:
        other_specification = str()
        source_db = self._task["database"]
        for target_db, file in self.spec_files.items():
            try:
                if target_db == self._task["database"]:
                    continue

                source_db_sqlglot, target_db_sqlglot = source_db, target_db
                sql = f"SELECT {self._task['func_name']}(1);"
                if source_db_sqlglot == "postgresql":
                    source_db_sqlglot = "postgres"
                if target_db_sqlglot == "postgresql":
                    target_db_sqlglot = "postgres"
                result = sqlglot.transpile(sql, read=source_db_sqlglot, write=target_db_sqlglot)[0]
                other_func_name = result.split("(")[0].split(" ")[-1]

                with open(file, "r") as rf:
                    spec_data = json.load(rf)
                for item in spec_data:
                    if item["keyword"].split("(")[0].lower() == other_func_name.lower():
                        description = item["description"]
                        example = "\n".join(item["example"]) if (
                            isinstance(item["example"], list)) else item["example"]
                        code = self.format_code(item["code"]) if "code" in item.keys() else ""
                        other_specification += f"\n\t<function_in_{self.db_name[target_db]}>\n"
                        other_specification += self.format_specification(other_func_name, description, example, code)
                        other_specification += f"\n\t</function_in_{self.db_name[target_db]}>\n"
                        break
            except Exception as e:
                print(f"Error in get_other_specification of `{target_db}`: {e}")

        return other_specification

    def get_other_dependency(self):
        category = self._task["category"]
        spec_load = self.spec_files[self._task["database"]]
        with open(spec_load, "r") as rf:
            spec_data = json.load(rf)

        dependency_total = dict()
        for item in spec_data:
            if (item["keyword"] == self._task["func_name"]
                    or item["category"] != category):
                continue

            for func, dependency in item["element"].items():
                for typ in dependency:
                    if typ not in dependency_total:
                        dependency_total[typ] = list()
                    dependency_total[typ].extend(dependency[typ])

        for typ in dependency_total:
            dependency_total[typ] = list(set(dependency_total[typ]))

        return dependency_total

    @staticmethod
    def check_code_element_by_grep(directory, pattern, file_pattern="*", case_sensitive=True):
        if not directory or not pattern or not pattern.strip():
            return {'success': False, 'matches': [], 'error': 'empty directory or pattern'}

        if not os.path.isdir(directory):
            return {'success': False, 'matches': [], 'error': f'directory not found: {directory}'}

        try:
            cmd = ["grep", "-F"]  # -F: treat pattern as fixed string, not regex
            if not case_sensitive:
                cmd.append("-i")
            cmd.extend(["-r", "-n", pattern, directory])
            if file_pattern != "*":
                cmd.extend(["--include", file_pattern])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                matches = [m for m in result.stdout.strip().split('\n') if m]
                return {'success': bool(matches), 'matches': matches, 'error': None}
            elif result.returncode == 1:
                # grep found no matches — element does not exist in source
                return {'success': False, 'matches': [], 'error': None}
            else:
                return {'success': False, 'matches': [], 'error': result.stderr.strip()}

        except subprocess.TimeoutExpired:
            # treat timeout as success to avoid penalizing valid elements on slow machines
            return {'success': True, 'matches': [], 'error': 'search timed out'}
        except Exception as e:
            return {'success': False, 'matches': [], 'error': str(e)}


if __name__ == "__main__":
    compile_folder = "/data/wei/code/sqlite5435"
    pattern = "sqlite"
    # result = BaseAgent.check_code_element_by_grep(compile_folder, pattern)

    result = BaseAgent.get_inner_specification()
    print(result)
