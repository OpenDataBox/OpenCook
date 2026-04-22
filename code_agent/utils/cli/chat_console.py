# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Chat Console — prompt_toolkit input + Rich scrolling output, non-fullscreen."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
from typing import TYPE_CHECKING

try:
    from typing import override
except ImportError:
    def override(func):
        return func

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from code_agent.agent.agent_basics import AgentExecution, AgentState, AgentStep, AgentStepState
from code_agent.utils.cli.cli_console import (
    AGENT_STATE_INFO,
    CLIConsole,
    ConsoleMode,
    ConsoleStep,
    ToolApprovalRequest,
)

if TYPE_CHECKING:
    from code_agent.session.schema import SessionMeta

logger = logging.getLogger(__name__)

# ── Spinner frames ─────────────────────────────────────────────────────────────

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ── Tool icon mapping (aligned with DBCooker real tool set) ───────────────────
# Each entry: tool_name → (icon, display_mode)
#   display_mode: "inline" | "block" | "diff" | "summary"

TOOL_ICONS: dict[str, tuple[str, str]] = {
    "bash":                        ("$",  "block"),
    "skill":                       ("→",  "inline"),
    "sequentialthinking":          ("◉",  "inline"),
    "str_replace_based_edit_tool": ("←",  "diff"),
    "task_done":                   ("✓",  "inline"),
    "test_subagent":               ("•",  "inline"),
    "plan_subagent":               ("•",  "inline"),
    "database_verify":             ("✱",  "inline"),
    "database_execute":            ("$",  "block"),
    "json_edit_tool":              ("←",  "summary"),
    "_default":                    ("⚙",  "inline"),
}

# ── DBCooker ASCII banner ──────────────────────────────────────────────────────

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


# ── Slash command completion ───────────────────────────────────────────────────

_SLASH_COMMANDS = [
    "/help", "/status", "/new", "/resume", "/fork",
    "/rename", "/plan", "/verify", "/clear", "/compact", "/permissions",
]


class _SlashCompleter(Completer):
    """Completes slash commands only when the input starts with '/'."""

    def get_completions(self, document, _complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for cmd in _SLASH_COMMANDS:
            if cmd.startswith(text):
                # Replace everything typed so far with the full command name
                yield Completion(cmd, start_position=-len(text), display=cmd)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_prompt_session() -> PromptSession:
    """Create a configured prompt_toolkit PromptSession with history and slash completion."""
    return PromptSession(
        history=InMemoryHistory(),
        auto_suggest=AutoSuggestFromHistory(),
        completer=_SlashCompleter(),
        complete_while_typing=False,  # only complete on Tab, not after every keystroke
    )


def _tool_icon_mode(tool_name: str) -> tuple[str, str]:
    """Return (icon, display_mode) for a tool name."""
    return TOOL_ICONS.get(tool_name, TOOL_ICONS["_default"])


# ── ChatConsole ────────────────────────────────────────────────────────────────

class ChatConsole(CLIConsole):
    """Stable chat-style interactive console.

    - Does not take over the whole terminal (no alternate screen)
    - prompt_toolkit manages input (with history, auto-suggest)
    - Rich Console.print() manages scrolling history output
    - rich.live.Live(transient=True) is used only for the active step spinner
    """

    def __init__(
        self,
        mode: ConsoleMode = ConsoleMode.INTERACTIVE,
    ):
        super().__init__(mode)
        self.console = Console(highlight=False, markup=True)
        # Live spinner — only active while a step is in progress
        self._live: Live | None = None
        self._live_text: str = ""
        self._current_state: AgentStepState | None = None
        # Spinner background task (created in begin_turn/begin_subagent_run)
        self._spin_task: asyncio.Task | None = None
        self._spin_stop: asyncio.Event = asyncio.Event()
        # prompt_toolkit input session
        self._prompt_session: PromptSession = _make_prompt_session()
        # Session-level metadata (updated by session_start/session_switch)
        self._session_meta: SessionMeta | None = None
        self._banner_printed: bool = False

    # ── Live spinner management ────────────────────────────────────────────────

    def _start_live(self) -> None:
        """Start a transient Live spinner (no-op if already started)."""
        if self._live and self._live.is_started:
            return
        self._live = Live(
            console=self.console,
            auto_refresh=False,
            transient=True,           # stop() clears the line cleanly
            vertical_overflow="crop", # never spill into history area
        )
        self._live.start()

    def _stop_live(self) -> None:
        """Stop the Live spinner. transient=True ensures it disappears without residue."""
        if self._live and self._live.is_started:
            self._live.stop()
        self._live = None

    def _build_spinner_text(self, frame: str) -> Text:
        """Build the Rich Text renderable for the current spinner state."""
        color, emoji = AGENT_STATE_INFO.get(  # type: ignore[arg-type]
            self._current_state, ("white", "•")
        )
        text = Text()
        text.append(f"{frame} ", style=color)
        text.append(f"{emoji} ", style="")
        text.append(self._live_text, style="dim")
        return text

    async def _spin_loop(self, stop_event: asyncio.Event) -> None:
        """Refresh the Live spinner every 100 ms.

        Rich Console already queries terminal width on every refresh, so there
        is no need to explicitly stop/start Live on resize — doing so causes
        cursor-position glitches (especially over SSH).
        """
        idx = 0
        while not stop_event.is_set():
            if self._live and self._live.is_started:
                frame = _SPINNER_FRAMES[idx % len(_SPINNER_FRAMES)]
                self._live.update(self._build_spinner_text(frame), refresh=True)
            idx += 1
            await asyncio.sleep(0.1)
        self._stop_live()

    # ── Banner ─────────────────────────────────────────────────────────────────

    def _print_banner(self) -> None:
        self.console.print()
        for db_line, cooker_line in zip(_DB_LINES, _COOKER_SIDE):
            self.console.print(
                f"[bold bright_blue]{db_line}[/bold bright_blue]{cooker_line}"
            )
        self.console.print()
        self.console.rule(
            "[bold bright_blue]DBCooker[/bold bright_blue]"
            " [dim]— AI-powered database intelligence agent[/dim]",
            style="bright_blue",
        )
        self.console.print(
            "[dim]  Type [bold]/help[/bold] for commands."
            "  Type [bold]exit[/bold] or [bold]quit[/bold] to stop.[/dim]"
        )
        self.console.print()

    # ── Step rendering ─────────────────────────────────────────────────────────

    def _render_tool_call_block(
        self, tool_name: str, arguments: dict, result: str | None
    ) -> str:
        """Render a single tool call in inline/block/diff/summary style."""
        icon, mode = _tool_icon_mode(tool_name)
        lines: list[str] = []

        if mode == "block":
            # bash / database_execute: command + truncated output
            cmd = (
                arguments.get("command", "")
                or str(next(iter(arguments.values()), ""))
            )
            cmd_display = cmd.replace("\n", " ")
            if len(cmd_display) > 80:
                cmd_display = cmd_display[:77] + "..."
            lines.append(
                f"  [bold green]{escape(icon)}[/bold green]"
                f" [cyan]{escape(tool_name)}[/cyan]"
                f"  [dim]{escape(cmd_display)}[/dim]"
            )
            if result:
                out_lines = result.strip().splitlines()
                shown = out_lines[:10]
                lines.append(f"  [dim]{'─' * 20}[/dim]")
                for ol in shown:
                    lines.append(f"  [dim]{escape(ol)}[/dim]")
                if len(out_lines) > 10:
                    lines.append(
                        f"  [dim]... ({len(out_lines) - 10} more lines)[/dim]"
                    )
                lines.append(f"  [dim]{'─' * 20}[/dim]")

        elif mode == "diff":
            # str_replace_based_edit_tool: show unified diff
            path = arguments.get("path", "")
            lines.append(
                f"  [yellow]{escape(icon)}[/yellow]"
                f" [cyan]{escape(tool_name)}[/cyan]"
                f"  [dim]{escape(path)}[/dim]"
            )
            sub_cmd = arguments.get("command", "")
            if sub_cmd == "str_replace":
                old_lines = (arguments.get("old_str", "") or "").splitlines()
                new_lines = (arguments.get("new_str", "") or "").splitlines()
                diff = list(
                    difflib.unified_diff(
                        old_lines, new_lines,
                        fromfile=path, tofile=path, lineterm="",
                    )
                )
                shown = diff[:20]
                if shown:
                    diff_text = "\n".join(shown)
                    if len(diff) > 20:
                        diff_text += f"\n... ({len(diff) - 20} more lines)"
                    lines.append(f"[dim]{escape(diff_text)}[/dim]")
            elif sub_cmd == "create":
                lines.append("  [dim]created file[/dim]")
            elif sub_cmd == "view":
                lines.append("  [dim]viewed file[/dim]")

        elif mode == "summary":
            # json_edit_tool: structured change summary
            op = arguments.get("operation", "?")
            json_path = arguments.get("json_path", "")
            lines.append(
                f"  [yellow]{escape(icon)}[/yellow]"
                f" [cyan]{escape(tool_name)}[/cyan]"
                f"  [dim]{escape(op)} {escape(json_path)}[/dim]"
            )

        else:
            # inline: short single-line summary
            first_val = (
                str(next(iter(arguments.values()), "")) if arguments else ""
            )
            if len(first_val) > 60:
                first_val = first_val[:57] + "..."
            lines.append(
                f"  [dim]{escape(icon)}[/dim]"
                f" [cyan]{escape(tool_name)}[/cyan]"
                f"  [dim]{escape(first_val)}[/dim]"
            )

        return "\n".join(lines)

    def _print_completed_step(
        self,
        agent_step: AgentStep,
        agent_execution: AgentExecution | None = None,
    ) -> None:
        """Stop live, print the step content as rule + plain text, then restart live."""
        # _stop_live() is mandatory before Console.print() — stdout ownership rule
        self._stop_live()
        color, emoji = AGENT_STATE_INFO.get(agent_step.state, ("white", "❓"))
        self.console.rule(
            f"{emoji} [bold {color}]Step {agent_step.step_number}[/bold {color}]",
            style=color,
        )

        # LLM response snippet
        if agent_step.llm_response and agent_step.llm_response.content:
            resp = agent_step.llm_response.content.strip()
            if len(resp) > 400:
                resp = resp[:397] + "..."
            self.console.print(f"[dim]{escape(resp)}[/dim]")

        # Tool calls with results
        if agent_step.tool_calls:
            for tc in agent_step.tool_calls:
                args = tc.arguments or {}
                result_str: str | None = None
                for tr in agent_step.tool_results or []:
                    if tr.call_id == tc.call_id:
                        result_str = tr.result
                        break
                self.console.print(self._render_tool_call_block(tc.name, args, result_str))

        # Reflection
        if agent_step.reflection:
            ref = agent_step.reflection.strip()
            if len(ref) > 200:
                ref = ref[:197] + "..."
            self.console.print(f"[magenta]💭 {escape(ref)}[/magenta]")

        # Error
        if agent_step.error:
            self.console.print(f"[red]❌ {escape(agent_step.error)}[/red]")

        # Token counts
        token_parts: list[str] = []
        if agent_step.llm_usage:
            u = agent_step.llm_usage
            token_parts.append(f"↑{u.input_tokens} ↓{u.output_tokens}")
        if agent_execution and agent_execution.total_tokens:
            t = agent_execution.total_tokens
            token_parts.append(f"total ↑{t.input_tokens} ↓{t.output_tokens}")
        if token_parts:
            self.console.print(f"[dim]tokens: {' | '.join(token_parts)}[/dim]")

        # Restart spinner only if the spin task is still running (more steps may follow)
        if self._spin_task and not self._spin_stop.is_set():
            self._start_live()

    def _print_execution_summary(self) -> None:
        """Print the final execution summary (rule + plain text, no Panel)."""
        if not self.agent_execution:
            return
        success = self.agent_execution.success
        color = "green" if success else "red"
        icon = "✅" if success else "❌"

        self.console.rule(
            f"[bold {color}]Execution Summary[/bold {color}]", style=color
        )
        task_str = str(self.agent_execution.task)
        if len(task_str) > 60:
            task_str = task_str[:57] + "..."
        self.console.print(f"  [dim]Task  [/dim] {escape(task_str)}")
        self.console.print(
            f"  [dim]Result[/dim] {icon} {'Success' if success else 'Failed'}"
        )
        self.console.print(f"  [dim]Steps [/dim] {len(self.agent_execution.steps)}")
        self.console.print(
            f"  [dim]Time  [/dim] {self.agent_execution.execution_time:.2f}s"
        )
        if self.agent_execution.total_tokens:
            t = self.agent_execution.total_tokens
            total = t.input_tokens + t.output_tokens
            self.console.print(
                f"  [dim]Tokens[/dim] ↑{t.input_tokens} ↓{t.output_tokens}"
                f"  total {total}"
            )

        if self.agent_execution.final_result:
            self.console.rule("[bold]Final Result[/bold]", style=color)
            self.console.print(Markdown(self.agent_execution.final_result))

    # ── Approval rendering ─────────────────────────────────────────────────────

    def _render_approval_preview(self, req: ToolApprovalRequest) -> None:
        """Render the tool approval preview (diff, command, summary, or inline)."""
        self.console.print(
            f"\n  [bold yellow]Tool Request:[/bold yellow]"
            f" [cyan]{escape(req.tool_name)}[/cyan]"
        )
        if req.preview_kind == "command":
            self.console.rule(style="green")
            self.console.print(
                f"  [bold green]$[/bold green] {escape(req.preview_text)}"
            )
            self.console.rule(style="green")
        elif req.preview_kind == "diff":
            self.console.rule(style="yellow")
            self.console.print(Syntax(req.preview_text, "diff", theme="monokai"))
            self.console.rule(style="yellow")
        elif req.preview_kind == "summary":
            self.console.print(f"  {escape(req.preview_text)}")
        else:  # "inline"
            self.console.print(f"  [dim]{escape(req.preview_text)}[/dim]")

    # ── CLIConsole abstract method overrides ───────────────────────────────────

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
                self._current_state = state
                self._live_text = (
                    f"Step {agent_step.step_number}"
                    f" — {state.value.replace('_', ' ')}"
                )

            elif state == AgentStepState.CALLING_TOOL:
                self._current_state = state
                if agent_step.tool_calls:
                    names = ", ".join(tc.name for tc in agent_step.tool_calls)
                    self._live_text = f"Step {agent_step.step_number} — {names}"
                else:
                    self._live_text = f"Step {agent_step.step_number} — calling tool"

            elif state in (AgentStepState.COMPLETED, AgentStepState.ERROR):
                cs = self.console_step_history[agent_step.step_number]
                if not cs.agent_step_printed:
                    self._print_completed_step(agent_step, agent_execution)
                    cs.agent_step_printed = True

        self.agent_execution = agent_execution

    @override
    async def start(self) -> None:
        """Legacy no-op: turn lifecycle is managed by begin_turn/end_turn."""
        logger.debug("legacy console method called: start")

    @override
    def print_task_details(self, details: dict[str, str]) -> None:
        """Legacy no-op: task context is displayed via begin_turn."""
        logger.debug("legacy console method called: print_task_details")

    @override
    def print(self, message: str, color: str = "blue", bold: bool = False) -> None:
        # _stop_live() is mandatory before Console.print() — stdout ownership rule
        self._stop_live()
        # escape() first so caller-supplied text (e.g. "[Plan]") is never parsed as markup
        safe = escape(message)
        safe = f"[bold]{safe}[/bold]" if bold else safe
        safe = f"[{color}]{safe}[/{color}]"
        self.console.print(safe)

    def debug_step(
        self,
        agent_step: AgentStep,
        agent_execution: AgentExecution | None = None,
        *,
        agent_type: str | None = None,
    ) -> None:
        if not self.is_debug:
            return

        def _clip(text: str, limit: int = 1400) -> str:
            t = (text or "").strip()
            return t if len(t) <= limit else (t[: limit - 1] + "…")

        def _pretty(value) -> str:
            if value is None:
                return ""
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, indent=2)
            return str(value)

        self._stop_live()
        title = f"[bold magenta]DEBUG[/bold magenta]  Step {agent_step.step_number}"
        if agent_type:
            title += f"  •  {escape(agent_type)}"

        lr = agent_step.llm_response
        usage = lr.usage if lr else None
        tool_calls = agent_step.tool_calls or (lr.tool_calls if lr else None) or []
        tool_results = agent_step.tool_results or []

        meta = Table(show_header=False, expand=True, pad_edge=False)
        meta.add_column(style="dim", width=16)
        meta.add_column()
        meta.add_row("State", escape(getattr(agent_step.state, "value", str(agent_step.state))))
        if lr and lr.model:
            meta.add_row("Model", escape(lr.model))
        if lr and lr.finish_reason:
            meta.add_row("Finish", escape(lr.finish_reason))
        if usage:
            meta.add_row(
                "Tokens",
                escape(
                    f"in={usage.input_tokens} out={usage.output_tokens}"
                    f" cache_write={usage.cache_creation_input_tokens} cache_read={usage.cache_read_input_tokens}"
                    f" reasoning={usage.reasoning_tokens}"
                ),
            )

        layout = Table.grid(expand=True)
        layout.add_row(meta)

        if agent_step.thought and agent_step.thought.strip():
            layout.add_row(
                Panel(
                    Text(_clip(agent_step.thought, limit=1800), overflow="fold"),
                    title="[dim]Thought[/dim]",
                    border_style="bright_black",
                )
            )

        if lr and (lr.content or "").strip():
            layout.add_row(
                Panel(
                    Text(_clip(lr.content, limit=1800), overflow="fold"),
                    title="[dim]LLM Output[/dim]",
                    border_style="bright_black",
                )
            )

        if tool_calls:
            tc_table = Table(show_header=True, expand=True)
            tc_table.add_column("#", style="dim", width=3, justify="right")
            tc_table.add_column("Tool", style="cyan", width=22, overflow="fold")
            tc_table.add_column("Call ID", style="dim", width=18, overflow="fold")
            tc_table.add_column("Arguments", overflow="fold")
            for i, tc in enumerate(tool_calls, start=1):
                tc_table.add_row(
                    str(i),
                    escape(tc.name),
                    escape(tc.call_id),
                    escape(_clip(_pretty(tc.arguments), limit=900)),
                )
            layout.add_row(
                Panel(tc_table, title="[dim]Tool Calls[/dim]", border_style="bright_black")
            )

        if tool_results:
            tr_table = Table(show_header=True, expand=True)
            tr_table.add_column("#", style="dim", width=3, justify="right")
            tr_table.add_column("Tool", style="cyan", width=22, overflow="fold")
            tr_table.add_column("Call ID", style="dim", width=18, overflow="fold")
            tr_table.add_column("OK", style="dim", width=4, justify="center")
            tr_table.add_column("Result / Error", overflow="fold")
            for i, tr in enumerate(tool_results, start=1):
                payload = tr.result if tr.success else (tr.error or tr.result or "")
                tr_table.add_row(
                    str(i),
                    escape(tr.name),
                    escape(tr.call_id),
                    "Y" if tr.success else "N",
                    escape(_clip(payload, limit=900)),
                )
            layout.add_row(
                Panel(tr_table, title="[dim]Tool Results[/dim]", border_style="bright_black")
            )

        next_messages = None
        if isinstance(agent_step.extra, dict):
            next_messages = agent_step.extra.get("next_messages")
        if isinstance(next_messages, list) and next_messages:
            nm_table = Table(show_header=True, expand=True)
            nm_table.add_column("#", style="dim", width=3, justify="right")
            nm_table.add_column("Role", style="dim", width=10)
            nm_table.add_column("Type", style="dim", width=12)
            nm_table.add_column("Content", overflow="fold")
            for i, m in enumerate(next_messages, start=1):
                role = str(m.get("role", "")) if isinstance(m, dict) else ""
                kind = ""
                payload = ""
                if isinstance(m, dict) and "tool_result" in m:
                    kind = "tool_result"
                    tr = m.get("tool_result") or {}
                    payload = _pretty(tr) if isinstance(tr, (dict, list)) else str(tr)
                elif isinstance(m, dict) and "tool_call" in m:
                    kind = "tool_call"
                    tc = m.get("tool_call") or {}
                    payload = _pretty(tc) if isinstance(tc, (dict, list)) else str(tc)
                else:
                    kind = "content"
                    if isinstance(m, dict):
                        payload = str(m.get("content", "") or "")
                    else:
                        payload = str(m)
                nm_table.add_row(
                    str(i),
                    escape(_clip(role, limit=60)),
                    escape(_clip(kind, limit=60)),
                    escape(_clip(payload, limit=1200)),
                )
            layout.add_row(
                Panel(nm_table, title="[dim]Next Messages[/dim]", border_style="bright_black")
            )

        if agent_step.reflection:
            layout.add_row(
                Panel(
                    Text(_clip(agent_step.reflection, limit=1200), overflow="fold"),
                    title="[dim]Reflection[/dim]",
                    border_style="bright_black",
                )
            )

        if agent_step.error:
            layout.add_row(
                Panel(
                    Text(_clip(agent_step.error, limit=1200), overflow="fold"),
                    title="[dim]Step Error[/dim]",
                    border_style="red",
                )
            )

        self.console.print(Panel(layout, title=title, border_style="magenta"))

    @override
    def get_task_input(self) -> str | None:
        """Legacy sync path — always returns None.

        ChatConsole uses get_task_input_async() exclusively.  None triggers the
        runner's break condition, which is safe (runner has a None guard).
        """
        logger.warning(
            "ChatConsole.get_task_input() called on legacy sync path;"
            " use get_task_input_async() instead"
        )
        return None

    @override
    def get_working_dir_input(self) -> str | None:
        """Legacy sync path — always returns None.

        Caller (cli.py) falls back to os.getcwd() when None is returned.
        """
        logger.warning(
            "ChatConsole.get_working_dir_input() called on legacy sync path"
        )
        return None

    @override
    def stop(self) -> None:
        """Legacy no-op."""
        logger.debug("legacy console method called: stop")

    # ── Session-level lifecycle ────────────────────────────────────────────────

    @override
    async def session_start(self, session_meta: SessionMeta) -> None:
        """Print banner once and display initial session info."""
        self._session_meta = session_meta
        if not self._banner_printed:
            self._print_banner()
            self._banner_printed = True
        title = f" • {escape(session_meta.title)}" if session_meta.title else ""
        self.console.print(
            f"[dim]─── session: {escape(session_meta.session_id)}{title} ───[/dim]"
        )

    @override
    async def session_stop(self) -> None:
        """Stop any active spinner task and clean up Live."""
        if self._spin_task and not self._spin_task.done():
            self._spin_stop.set()
            await self._spin_task
            self._spin_task = None
        self._stop_live()

    @override
    def session_switch(self, new_session_meta: SessionMeta) -> None:
        """Update session metadata and print a one-line switch indicator."""
        self._session_meta = new_session_meta
        title = f" • {escape(new_session_meta.title)}" if new_session_meta.title else ""
        self.console.print(
            f"[dim]─── session: {escape(new_session_meta.session_id)}{title} ───[/dim]"
        )

    @override
    def terminal_clear(self) -> None:
        """Clear the terminal via Rich Console."""
        self.console.clear()

    # ── Turn-level lifecycle ───────────────────────────────────────────────────

    @override
    async def begin_turn(self, user_input: str) -> None:
        """Reset per-turn state, display user input panel, start spinner."""
        self.console_step_history = {}
        self.agent_execution = None
        self._current_state = None
        self._live_text = ""
        # Stop any stale spinner from a previous (failed) turn
        if self._spin_task and not self._spin_task.done():
            self._spin_stop.set()
            await self._spin_task
            self._spin_task = None
        self._stop_live()
        # Print user input rule (live must be stopped first — no Panel to avoid resize garbling)
        short = user_input[:80] + ("…" if len(user_input) > 80 else "")
        self.console.rule(
            f"[bold bright_blue]❯ {escape(short)}[/bold bright_blue]",
            style="bright_blue",
        )
        # Start live spinner then launch the spin loop task
        self._start_live()
        self._spin_stop = asyncio.Event()
        self._spin_task = asyncio.create_task(self._spin_loop(self._spin_stop))

    @override
    async def end_turn(self, execution: AgentExecution | None) -> None:
        """Stop spinner and print summaries if execution succeeded."""
        if self._spin_task is not None:
            self._spin_stop.set()
            await self._spin_task
            self._spin_task = None
        # _spin_loop already calls _stop_live() on exit; this is idempotent cleanup
        self._stop_live()

        if execution is not None:
            self.agent_execution = execution
            self._print_execution_summary()

    @override
    async def begin_subagent_run(self) -> None:
        """Reset per-turn state and start spinner (no user input panel)."""
        self.console_step_history = {}
        self.agent_execution = None
        self._current_state = None
        self._live_text = ""
        if self._spin_task and not self._spin_task.done():
            self._spin_stop.set()
            await self._spin_task
            self._spin_task = None
        self._stop_live()
        self._start_live()
        self._spin_stop = asyncio.Event()
        self._spin_task = asyncio.create_task(self._spin_loop(self._spin_stop))

    # ── Async input ────────────────────────────────────────────────────────────

    def _get_prompt_text(self) -> str:
        """Build the input prompt string, reflecting the current session context."""
        if self._session_meta and self._session_meta.title:
            title = self._session_meta.title[:20]
            return f"  [{title}] > "
        return "  > "

    @override
    async def get_task_input_async(self) -> str | None:
        """Async input via prompt_toolkit. Returns None on EOF/Ctrl-C/exit."""
        try:
            raw = await self._prompt_session.prompt_async(self._get_prompt_text())
        except (EOFError, KeyboardInterrupt):
            return None
        stripped = raw.strip()
        if stripped.lower() in ("exit", "quit"):
            return None
        return stripped  # may be "" — runner will loop and ask again

    # ── Tool approval ──────────────────────────────────────────────────────────

    @override
    async def request_tool_approval_async(self, req: ToolApprovalRequest) -> str:
        """Show tool preview and prompt for approval using prompt_toolkit.

        Spinner is stopped before printing (stdout ownership rule) and
        restarted after approval so it shows during tool execution.
        """
        # _stop_live() is mandatory before any prompt_async() or Console.print()
        self._stop_live()
        self._render_approval_preview(req)
        self.console.print(
            "[dim]  y = approve once   t = approve this tool for turn"
            "   s = approve all remaining   n = deny[/dim]"
        )
        while True:
            try:
                raw = await self._prompt_session.prompt_async("  Approve? (y/t/s/n) ")
            except (EOFError, KeyboardInterrupt):
                # Restore spinner: the turn's other steps may still run after denial
                self._start_live()
                return "n"
            choice = raw.strip().lower()
            if choice in ("y", "t", "s", "n"):
                # Always restore spinner regardless of choice — the turn continues
                # and subsequent steps need visible status even after a denial.
                self._start_live()
                return choice
            self.console.print("[red]  Invalid. Enter y / t / s / n.[/red]")

