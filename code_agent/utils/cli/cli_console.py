# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Base CLI Console classes for OpenCook."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.table import Table

from code_agent.agent.agent_basics import AgentExecution, AgentStep, AgentStepState

if TYPE_CHECKING:
    from pathlib import Path

    from code_agent.session.schema import SessionMeta


class ConsoleMode(Enum):
    """Console operation modes."""

    RUN = "run"  # Execute single task and exit
    INTERACTIVE = "interactive"  # Take multiple tasks from user input


class ConsoleType(Enum):
    """Available console types."""

    SIMPLE = "simple"  # Simple text-based console
    RICH = "rich"  # Rich textual-based console with TUI
    CHAT = "chat"  # Chat-style console (prompt_toolkit + Rich, non-fullscreen)


@dataclass
class ToolApprovalRequest:
    """Request object passed to request_tool_approval / request_tool_approval_async.

    Carries everything the console needs to render a meaningful approval prompt
    without the console having to understand raw tool arguments.
    """

    tool_name: str
    preview_text: str  # rendered summary / diff / command text
    preview_kind: str  # "command" | "diff" | "summary" | "inline"


AGENT_STATE_INFO = {
    AgentStepState.THINKING: ("blue", "🤔"),
    AgentStepState.CALLING_TOOL: ("yellow", "🔧"),
    AgentStepState.REFLECTING: ("magenta", "💭"),
    AgentStepState.COMPLETED: ("green", "✅"),
    AgentStepState.ERROR: ("red", "❌"),
}


@dataclass
class ConsoleStep:
    """Represents a console step and its rendering state."""

    agent_step: AgentStep
    agent_step_printed: bool = False
    # Live preview tracking (for replace-on-complete animation)
    tool_call_preview_printed: bool = False
    preview_log_items: int = 0
    preview_exit_items: int = 0
    preview_log_index: int | None = None
    preview_exit_index: int | None = None


class CLIConsole(ABC):
    """Base class for CLI console implementations."""

    def __init__(self, mode: ConsoleMode = ConsoleMode.RUN):
        """Initialize the CLI console.

        Args:
            mode: Console operation mode (run or interactive)
        """
        self.mode: ConsoleMode = mode
        self.console_step_history: dict[int, ConsoleStep] = {}
        self.agent_execution: AgentExecution | None = None

    # ── Legacy turn-level interface (kept for batch / non-interactive paths) ──

    @abstractmethod
    async def start(self):
        """Start the console display. Should be implemented by subclasses."""
        pass

    @abstractmethod
    def update_status(
        self, agent_step: AgentStep | None = None, agent_execution: AgentExecution | None = None
    ):
        """Update the console with agent status."""
        pass

    @abstractmethod
    def print_task_details(self, details: dict[str, str]):
        """Print initial task configuration details."""
        pass

    @abstractmethod
    def print(self, message: str, color: str = "blue", bold: bool = False):
        """Print a message to the console."""
        pass

    def write_rich(self, renderable) -> None:
        """Write a Rich renderable to the console (TUI) or fall back to str(renderable)."""
        pass

    @abstractmethod
    def get_task_input(self) -> str | None:
        """Get task input from user (synchronous, for legacy / batch paths).

        Returns:
            Task string or None if user wants to exit
        """
        pass

    @abstractmethod
    def get_working_dir_input(self) -> str | None:
        """Get working directory input from user (synchronous, legacy path).

        Returns:
            Working directory path, or None to use os.getcwd() fallback.
        """
        pass

    @abstractmethod
    def stop(self):
        """Stop the console and cleanup resources."""
        pass

    # ── Tool approval ─────────────────────────────────────────────────────────

    def request_tool_approval(self, req: ToolApprovalRequest) -> str:
        """Ask the user to approve a tool call (synchronous).

        The default always approves so batch mode is completely unaffected.

        Returns:
            'y' approve once | 't' approve this tool for turn |
            's' approve all remaining | 'n' deny
        """
        return "y"

    async def request_tool_approval_async(self, req: ToolApprovalRequest) -> str:
        """Async wrapper around request_tool_approval.

        Default implementation runs the synchronous version in a thread-pool
        executor so SimpleCLIConsole (which uses input()) can serve as a
        fallback without blocking the event loop.

        ChatConsole overrides this directly with prompt_async().
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.request_tool_approval, req)

    # ── Session-level lifecycle ───────────────────────────────────────────────

    async def session_start(self, session_meta: SessionMeta) -> None:
        """Called once by SessionRunner.run() before the input loop begins.

        Implementations should print the banner, initialise prompt sessions, etc.
        Default is a no-op (safe for batch / non-interactive consoles).
        """
        pass

    async def session_stop(self) -> None:
        """Called in the finally block of SessionRunner.run().

        Implementations should clean up Live objects, prompt sessions, etc.
        Default is a no-op.
        """
        pass

    def session_switch(self, new_session_meta: SessionMeta) -> None:
        """Called after /new, /resume, /fork, /rename changes the active session.

        Implementations should update the prompt prefix or print a switch line.
        Default is a no-op.
        """
        pass

    def terminal_clear(self) -> None:
        """Clear the terminal (called by /clear command).

        Default is a no-op; subclasses override with os.system or Console.clear().
        """
        pass

    def turn_report_ready(self, report_path: "Path") -> None:
        """Called by runner after the HTML trajectory report has been written.

        Default implementation prints the path for non-TUI consoles.
        TextualConsole overrides this to render the styled trajectory line.
        """
        self.print(f"Turn report: {report_path}", color="blue")

    # ── Turn-level lifecycle ──────────────────────────────────────────────────

    async def begin_turn(self, user_input: str) -> None:
        """Called at the start of every user turn, before the agent runs.

        Resets per-turn state and starts the spinner.  async because
        ChatConsole needs to await the spinner task creation.

        Default implementation resets console_step_history and agent_execution.
        """
        self.console_step_history = {}
        self.agent_execution = None

    async def end_turn(self, execution: AgentExecution | None) -> None:
        """Called in the finally block of _run_turn(), always executes.

        Stops the spinner.  If execution is not None, prints the turn summary.
        async because ChatConsole needs to await the spinner task cancellation
        to prevent stdout races with the next prompt.

        Default is a no-op (SimpleCLIConsole overrides with real cleanup).
        """
        pass

    async def begin_subagent_run(self) -> None:
        """Called before slash-command step-mechanism runs (/plan, etc.).

        Like begin_turn() but does NOT print a user-input panel.
        async for the same reason as begin_turn().

        Default resets per-turn state (same as begin_turn default).
        """
        self.console_step_history = {}
        self.agent_execution = None

    # ── Async input ───────────────────────────────────────────────────────────

    async def get_task_input_async(self) -> str | None:
        """Async version of get_task_input().

        Default wraps the synchronous version via run_in_executor so
        SimpleCLIConsole's input()-based fallback works without blocking.

        ChatConsole overrides this with prompt_async().
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_task_input)

def generate_agent_step_table(agent_step: AgentStep) -> Table:
    """Log an agent step to the console."""
    color, emoji = AGENT_STATE_INFO.get(agent_step.state, ("white", "❓"))

    # Print the step state in a table
    table = Table(show_header=False, width=120)
    table.add_column("Step Number", style="cyan", width=15)
    table.add_column(f"{agent_step.step_number}", style="green", width=105)

    # Add status row
    table.add_row(
        "Status",
        f"[{color}]{emoji} Step {agent_step.step_number}: {agent_step.state.value.title()}[/{color}]",
    )

    # Add LLM response row
    if agent_step.llm_response and agent_step.llm_response.content:
        table.add_row("LLM Response", f"💬 {escape(agent_step.llm_response.content)}")

    # Add tool calls row
    if agent_step.tool_calls:
        tool_names = [f"[cyan]{escape(call.name)}[/cyan]" for call in agent_step.tool_calls]
        table.add_row("Tools", f"🔧 {', '.join(tool_names)}")

        for tool_call in agent_step.tool_calls:
            # Build a tool call table with tool name, arguments and result
            tool_call_table = Table(show_header=False, width=100)
            tool_call_table.add_column("Arguments", style="green", width=50)
            tool_call_table.add_column("Result", style="green", width=50)
            tool_result_str = ""
            for tool_result in agent_step.tool_results or []:
                if tool_result.call_id == tool_call.call_id:
                    tool_result_str = tool_result.result or ""
                    break
            tool_call_table.add_row(escape(str(tool_call.arguments)), escape(tool_result_str))
            table.add_row(escape(tool_call.name), tool_call_table)

    # Add reflection row
    if agent_step.reflection:
        table.add_row("Reflection", f"💭 {escape(agent_step.reflection)}")

    # Add error row
    if agent_step.error:
        table.add_row("Error", f"❌ {escape(agent_step.error)}")

    return table

