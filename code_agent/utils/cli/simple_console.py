# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Simple CLI Console — DBCooker banner + live spinner + OpenCode-style step panels."""

import asyncio
import shutil
import sys
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from code_agent.agent.agent_basics import AgentExecution, AgentState, AgentStep, AgentStepState
from code_agent.utils.cli.cli_console import (
    AGENT_STATE_INFO,
    CLIConsole,
    ConsoleMode,
    ConsoleStep,
    ToolApprovalRequest,
)

# ── DBCooker ASCII banner ─────────────────────────────────────────────────────

_DB_LINES = (
    " ██████╗ ██████╗ ",
    " ██╔══██╗██╔══██╗",
    " ██║  ██║██████╔╝",
    " ██║  ██║██╔══██╗",
    " ██████╔╝██████╔╝",
    " ╚═════╝ ╚═════╝ ",
)

_COOKER_SIDE = (
    "",
    "  [bold white]C O O K E R[/bold white]",
    "  [dim]Agent[/dim]",
    "",
    "  [yellow]🍳[/yellow] [cyan]Database Intelligence[/cyan] [red]🔥[/red]",
    "",
)

# ── Raw ANSI helpers (for the status line — Rich can't write \r) ──────────────

_ANSI_COLORS: dict[str, str] = {
    "blue":    "\033[94m",
    "yellow":  "\033[33m",
    "magenta": "\033[95m",
    "green":   "\033[92m",
    "red":     "\033[91m",
    "cyan":    "\033[96m",
    "white":   "\033[97m",
}
_ANSI_RESET = "\033[0m"
_ANSI_DIM   = "\033[2m"

# ── Braille spinner ───────────────────────────────────────────────────────────

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ── Tool icon mapping (OpenCode-style) ────────────────────────────────────────
# Each entry: keyword_in_tool_name → Rich-markup icon used in step panels

_TOOL_ICONS_RICH: dict[str, str] = {
    "bash":      "[bold green]$[/bold green]",
    "shell":     "[bold green]$[/bold green]",
    "execute":   "[bold green]$[/bold green]",
    "run":       "[bold green]$[/bold green]",
    "read":      "[cyan]→[/cyan]",
    "list":      "[cyan]→[/cyan]",
    "glob":      "[magenta]✱[/magenta]",
    "grep":      "[magenta]✱[/magenta]",
    "search":    "[magenta]✱[/magenta]",
    "edit":      "[yellow]←[/yellow]",
    "write":     "[yellow]←[/yellow]",
    "create":    "[yellow]←[/yellow]",
    "patch":     "[yellow]←[/yellow]",
    "webfetch":  "[blue]%[/blue]",
    "fetch":     "[blue]%[/blue]",
    "websearch": "[blue]◈[/blue]",
}


def _enable_vt_on_windows() -> None:
    """Enable VT100 escape-code processing on Windows stdout (no-op elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong(0)
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        pass


def _tool_icon(tool_name: str) -> str:
    """Return Rich-markup icon for a tool name."""
    lower = tool_name.lower()
    for key, icon in _TOOL_ICONS_RICH.items():
        if key in lower:
            return icon
    return "[dim]⚙[/dim]"


class SimpleCLIConsole(CLIConsole):
    """Simple CLI console with DBCooker banner and live spinner animation."""

    def __init__(
        self,
        mode: ConsoleMode = ConsoleMode.RUN,
    ):
        super().__init__(mode)
        self.console: Console = Console()
        _enable_vt_on_windows()
        self._is_tty: bool = sys.stdout.isatty()
        self._term_width: int = shutil.get_terminal_size((80, 24)).columns
        self._banner_printed: bool = False
        # Spinner state — written by update_status, read by _spin_loop
        self._in_progress: bool = False
        self._spinner_text: str = ""
        self._spinner_state: AgentStepState | None = None
        self._paused: bool = False  # True while waiting for user input
        # Spinner task managed by begin_turn / begin_subagent_run / end_turn
        self._spin_task: asyncio.Task | None = None
        self._spin_stop: asyncio.Event = asyncio.Event()
        self._session_meta = None  # cached by session_start / session_switch

    # ── Low-level ANSI status line ────────────────────────────────────────────

    def _write_status(self, frame: str, color: str, emoji: str, desc: str) -> None:
        """Overwrite current line with a spinner frame + description."""
        if not self._is_tty:
            return
        w = shutil.get_terminal_size((80, 24)).columns
        max_desc = max(0, w - 8)
        if len(desc) > max_desc:
            desc = desc[:max_desc - 1] + "…"
        ansi_c = _ANSI_COLORS.get(color, "")
        sys.stdout.write(
            f"\r\033[K{ansi_c}{frame}{_ANSI_RESET} {emoji} {_ANSI_DIM}{desc}{_ANSI_RESET}"
        )
        sys.stdout.flush()

    def _clear_status(self) -> None:
        """Erase the status line (no-op when not a TTY)."""
        if not self._is_tty:
            return
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    # ── Banner ────────────────────────────────────────────────────────────────

    def _print_banner(self) -> None:
        self.console.print()
        for db_line, cooker_line in zip(_DB_LINES, _COOKER_SIDE):
            self.console.print(
                f"[bold bright_blue]{db_line}[/bold bright_blue]{cooker_line}"
            )
        self.console.print()
        self.console.print(
            Panel(
                "[bold bright_blue]DBCooker[/bold bright_blue]"
                " [dim]— AI-powered database intelligence agent[/dim]\n"
                "[dim]Type [bold]exit[/bold] or [bold]quit[/bold] to stop.[/dim]",
                border_style="bright_blue",
                padding=(0, 2),
            )
        )
        self.console.print()

    # ── Spinner loop ──────────────────────────────────────────────────────────

    async def _spin_loop(self, stop_event: asyncio.Event) -> None:
        """Refresh the status line every 100 ms. Polls terminal size for resize."""
        idx = 0
        while not stop_event.is_set():
            # Resize detection
            new_w = shutil.get_terminal_size((80, 24)).columns
            if new_w != self._term_width:
                self._term_width = new_w

            if self._in_progress and not self._paused:
                frame = _SPINNER_FRAMES[idx % len(_SPINNER_FRAMES)]
                color, emoji = AGENT_STATE_INFO.get(
                    self._spinner_state, ("white", "•")  # type: ignore[arg-type]
                )
                self._write_status(frame, color, emoji, self._spinner_text)

            idx += 1
            await asyncio.sleep(0.1)

        self._clear_status()

    # ── Step panel ────────────────────────────────────────────────────────────

    def _format_tool_calls_inline(self, agent_step: AgentStep) -> str:
        """Render tool calls as compact OpenCode-style inline lines."""
        lines: list[str] = []
        for tc in agent_step.tool_calls or []:
            icon = _tool_icon(tc.name)
            name = f"[cyan]{escape(tc.name)}[/cyan]"

            args = tc.arguments or {}
            if isinstance(args, dict) and args:
                k, v = next(iter(args.items()))
                v_str = str(v).replace("\n", " ")
                if len(v_str) > 50:
                    v_str = v_str[:47] + "..."
                args_str = f"[dim]{escape(k)}={escape(v_str)}[/dim]"
            else:
                raw = str(args).replace("\n", " ")
                if len(raw) > 50:
                    raw = raw[:47] + "..."
                args_str = f"[dim]{escape(raw)}[/dim]"

            result_str = ""
            for tr in agent_step.tool_results or []:
                if tr.call_id == tc.call_id:
                    res = (tr.result or "").strip()
                    first = res.split("\n")[0]
                    if len(first) > 60:
                        first = first[:57] + "..."
                    result_str = f"  [dim green]↩ {escape(first)}[/dim green]"
                    break

            lines.append(f"  {icon} {name}  {args_str}{result_str}")

        return "\n".join(lines)

    def _print_step_panel(
        self,
        agent_step: AgentStep,
        agent_execution: AgentExecution | None = None,
    ) -> None:
        color, emoji = AGENT_STATE_INFO.get(agent_step.state, ("white", "❓"))
        parts: list[str] = []

        # LLM response snippet
        if agent_step.llm_response and agent_step.llm_response.content:
            resp = agent_step.llm_response.content.strip()
            if len(resp) > 300:
                resp = resp[:297] + "..."
            parts.append(f"[dim]{escape(resp)}[/dim]")

        # Tool calls
        if agent_step.tool_calls:
            if parts:
                parts.append("")
            parts.append(self._format_tool_calls_inline(agent_step))

        # Reflection
        if agent_step.reflection:
            if parts:
                parts.append("")
            ref = agent_step.reflection.strip()
            if len(ref) > 200:
                ref = ref[:197] + "..."
            parts.append(f"[magenta]💭 {escape(ref)}[/magenta]")

        # Error
        if agent_step.error:
            parts.append(f"[red]❌ {escape(agent_step.error)}[/red]")

        # Token counts
        token_parts: list[str] = []
        if agent_step.llm_usage:
            u = agent_step.llm_usage
            token_parts.append(f"↑{u.input_tokens} ↓{u.output_tokens}")
        if agent_execution and agent_execution.total_tokens:
            t = agent_execution.total_tokens
            token_parts.append(f"total ↑{t.input_tokens} ↓{t.output_tokens}")
        if token_parts:
            if parts:
                parts.append("")
            parts.append(f"[dim]tokens: {' | '.join(token_parts)}[/dim]")

        content = "\n".join(parts) if parts else "[dim](no output)[/dim]"
        self.console.print(
            Panel(
                content,
                title=f"{emoji} [bold {color}]Step {agent_step.step_number}[/bold {color}]",
                border_style=color,
            )
        )

    # ── CLIConsole overrides ──────────────────────────────────────────────────

    @override
    def update_status(
        self,
        agent_step: AgentStep | None = None,
        agent_execution: AgentExecution | None = None,
    ) -> None:
        if agent_step:
            if agent_step.step_number not in self.console_step_history:
                self.console_step_history[agent_step.step_number] = ConsoleStep(agent_step)

            state = agent_step.state

            if state in (AgentStepState.THINKING, AgentStepState.REFLECTING):
                self._in_progress = True
                self._spinner_state = state
                self._spinner_text = (
                    f"Step {agent_step.step_number} — {state.value.replace('_', ' ')}"
                )

            elif state == AgentStepState.CALLING_TOOL:
                self._in_progress = True
                self._spinner_state = state
                if agent_step.tool_calls:
                    names = ", ".join(tc.name for tc in agent_step.tool_calls)
                    self._spinner_text = f"Step {agent_step.step_number} — {names}"
                else:
                    self._spinner_text = f"Step {agent_step.step_number} — calling tool"

            elif state in (AgentStepState.COMPLETED, AgentStepState.ERROR):
                cs = self.console_step_history[agent_step.step_number]
                if not cs.agent_step_printed:
                    self._in_progress = False
                    self._clear_status()
                    self._print_step_panel(agent_step, agent_execution)
                    cs.agent_step_printed = True

        self.agent_execution = agent_execution

    @override
    async def start(self) -> None:
        """Print banner (once), run spinner, wait for completion, print summary."""
        # Reset per-turn state so multi-turn interactive mode works correctly
        self.console_step_history = {}
        self.agent_execution = None

        if not self._banner_printed:
            self._print_banner()
            self._banner_printed = True

        stop_event = asyncio.Event()
        spinner_task = asyncio.create_task(self._spin_loop(stop_event))

        try:
            while self.agent_execution is None or (
                self.agent_execution.agent_state != AgentState.COMPLETED
                and self.agent_execution.agent_state != AgentState.ERROR
            ):
                await asyncio.sleep(0.2)
        finally:
            stop_event.set()
            await spinner_task

        if self.agent_execution:
            self._print_execution_summary()

    @override
    def print_task_details(self, details: dict[str, str]) -> None:
        renderable = ""
        for key, value in details.items():
            renderable += f"[bold]{key}:[/bold] {escape(str(value))}\n"
        self.console.print(
            Panel(
                renderable.strip(),
                title="[bold]Task Details[/bold]",
                border_style="bright_blue",
            )
        )

    @override
    def print(self, message: str, color: str = "blue", bold: bool = False) -> None:
        self._clear_status()  # erase spinner line before printing
        # escape() first so caller-supplied text (e.g. "[Plan]") is never parsed as markup
        safe = escape(message)
        safe = f"[bold]{safe}[/bold]" if bold else safe
        safe = f"[{color}]{safe}[/{color}]"
        self.console.print(safe)

    @override
    def get_task_input(self) -> str | None:
        if self.mode != ConsoleMode.INTERACTIVE:
            return None
        self._clear_status()
        self.console.print(
            "\n[bold bright_blue]❯[/bold bright_blue] [bold]Task:[/bold] ", end=""
        )
        try:
            task = input()
            if task.strip().lower() in ("exit", "quit"):
                return None
            return task
        except (EOFError, KeyboardInterrupt):
            return None

    @override
    def get_working_dir_input(self) -> str | None:
        if self.mode != ConsoleMode.INTERACTIVE:
            return None
        self._clear_status()
        self.console.print(
            "[bold bright_blue]❯[/bold bright_blue] [bold]Working Directory:[/bold] ", end=""
        )
        try:
            return input()
        except (EOFError, KeyboardInterrupt):
            return None

    @override
    def stop(self) -> None:
        pass

    # ── Session-level lifecycle ───────────────────────────────────────────────

    @override
    async def session_start(self, session_meta) -> None:
        """Print banner once at session start and cache session metadata."""
        self._session_meta = session_meta
        if not self._banner_printed:
            self._print_banner()
            self._banner_printed = True

    @override
    def session_switch(self, new_session_meta) -> None:
        """Update cached session metadata and print a one-line switch indicator."""
        self._session_meta = new_session_meta
        title = f" • {escape(new_session_meta.title)}" if new_session_meta.title else ""
        self.console.print(
            f"[dim]─── session: {escape(new_session_meta.session_id)}{title} ───[/dim]"
        )

    @override
    def terminal_clear(self) -> None:
        """Clear the terminal."""
        import os as _os
        _os.system("cls" if _os.name == "nt" else "clear")

    # ── Turn-level lifecycle ──────────────────────────────────────────────────

    @override
    async def begin_turn(self, user_input: str) -> None:
        """Reset per-turn state, display user input panel, start spinner."""
        self.console_step_history = {}
        self.agent_execution = None
        self._in_progress = False
        self._clear_status()
        self.console.print(
            Panel(
                escape(user_input),
                title="[bold bright_blue]Your Task[/bold bright_blue]",
                border_style="bright_blue",
                padding=(0, 2),
            )
        )
        self._spin_stop = asyncio.Event()
        self._spin_task = asyncio.create_task(self._spin_loop(self._spin_stop))

    @override
    async def end_turn(self, execution: AgentExecution | None) -> None:
        """Stop spinner and print summaries if execution succeeded."""
        if self._spin_task is not None:
            self._spin_stop.set()
            await self._spin_task
            self._spin_task = None

        if execution is not None:
            self.agent_execution = execution
            self._print_execution_summary()

    @override
    async def begin_subagent_run(self) -> None:
        """Reset per-turn state and start spinner (no user input panel)."""
        self.console_step_history = {}
        self.agent_execution = None
        self._in_progress = False
        self._clear_status()
        # Stop any spinner that begin_turn() already started; without this the
        # old task loses its handle and continues printing to the status line.
        if self._spin_task and not self._spin_task.done():
            self._spin_stop.set()
            await self._spin_task
            self._spin_task = None
        self._spin_stop = asyncio.Event()
        self._spin_task = asyncio.create_task(self._spin_loop(self._spin_stop))

    @override
    def request_tool_approval(self, req: ToolApprovalRequest) -> str:
        self._paused = True
        self._clear_status()
        icon = _tool_icon(req.tool_name)
        self.console.print(
            f"\n  {icon} [bold yellow]Tool Request:[/bold yellow]"
            f" [cyan]{escape(req.tool_name)}[/cyan]"
            f"  [dim]{escape(req.preview_text)}[/dim]"
        )
        self.console.print(
            "[dim]  y = approve once   t = approve this tool for turn"
            "   s = approve all remaining   n = deny[/dim]"
        )
        while True:
            self.console.print(
                "[bold yellow]  Approve?[/bold yellow] [dim](y/t/s/n)[/dim] ", end=""
            )
            try:
                choice = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                self._paused = False
                return "n"
            if choice in ("y", "t", "s", "n"):
                self._paused = False
                return choice
            self.console.print("[red]  Invalid input. Please enter y, t, s, or n.[/red]")

    def _print_execution_summary(self) -> None:
        if not self.agent_execution:
            return

        success = self.agent_execution.success
        color = "green" if success else "red"
        icon = "✅" if success else "❌"

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Metric", style="dim", min_width=14)
        table.add_column("Value", style="white")

        task_str = str(self.agent_execution.task)
        if len(task_str) > 60:
            task_str = task_str[:57] + "..."
        table.add_row("Task", task_str)
        table.add_row("Result", f"{icon} {'Success' if success else 'Failed'}")
        table.add_row("Steps", str(len(self.agent_execution.steps)))
        table.add_row("Time", f"{self.agent_execution.execution_time:.2f}s")

        if self.agent_execution.total_tokens:
            t = self.agent_execution.total_tokens
            total = t.input_tokens + t.output_tokens
            table.add_row("Tokens", f"↑{t.input_tokens} ↓{t.output_tokens}  total {total}")

        self.console.print(
            Panel(
                table,
                title=f"[bold {color}]Execution Summary[/bold {color}]",
                border_style=color,
            )
        )

        if self.agent_execution.final_result:
            self.console.print(
                Panel(
                    Markdown(self.agent_execution.final_result),
                    title="[bold]Final Result[/bold]",
                    border_style=color,
                )
            )

