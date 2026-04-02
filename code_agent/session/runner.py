# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""SessionRunner — manages the interactive turn loop with full LLM history continuity."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from code_agent.agent.agent import Agent
from code_agent.agent.agent_basics import AgentStepState
from code_agent.session.commands import SLASH_COMMANDS, SlashCommandParser
from code_agent.session.schema import SessionMeta, SessionTurn, TranscriptMessage
from code_agent.session.store import SessionStore, _llm_message_to_dict
from code_agent.utils.config import Config
from code_agent.utils.trajectory_report import write_trajectory_report

if TYPE_CHECKING:
    from code_agent.utils.cli import CLIConsole


class SessionRunner:
    """
    Drives an interactive session turn by turn.

    Context continuity is achieved by persisting the complete LLM message history
    (all messages including tool calls and results) to history.jsonl after each turn,
    then restoring it before the next turn via set_chat_history().  This mirrors the
    approach used by Claude Code and Cursor — the LLM always sees the full conversation.

    MCP lifecycle is managed entirely within run(), using a single asyncio event loop
    so that connections are not re-established on every turn.
    """

    def __init__(
        self,
        config: Config,
        store: SessionStore,
        session: SessionMeta,
        cli_console: "CLIConsole",
    ):
        self._config = config
        self._store = store
        self._session = session
        self._cli_console = cli_console
        self._mcp_tools: list = []
        self._mcp_agent = None  # CodeAgent instance that owns MCP connections
        self._last_user_input: str = ""  # most recent real user input, for /plan and /verify context
        self._turn_task: asyncio.Task | None = None  # current agent turn or slash-command task

    async def run(self) -> None:
        """Main interactive loop.  MCP is initialised once and reused across turns."""
        try:
            if self._config.code_agent.allow_mcp_servers:
                self._mcp_tools = await self._init_mcp_tools()
            else:
                self._mcp_tools = []
            await self._cli_console.session_start(self._session)
            while True:
                raw = await self._cli_console.get_task_input_async()
                if raw is None:
                    break
                raw = raw.strip()
                if not raw:
                    continue

                if raw.startswith("/"):
                    # Wrap slash commands in a cancellable task so Ctrl+C can
                    # interrupt /plan and /verify mid-execution.
                    await self._cli_console.begin_turn(raw)
                    self._turn_task = asyncio.create_task(self._handle_command(raw))
                    # Guard against the race where Ctrl+C arrived between
                    # begin_turn() (which resets _interrupt_requested) and
                    # create_task(): the flag is True but nothing was cancelled.
                    if getattr(self._cli_console, "_interrupt_requested", False):
                        self._turn_task.cancel()
                    try:
                        await self._turn_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        # Command error does not terminate the session.
                        logger.exception("Command failed: %s", exc)
                        self._cli_console.print(f"Command failed: {exc}", color="red")
                    finally:
                        await self._cli_console.end_turn(None)
                        self._turn_task = None
                    continue

                # Regular user input: wrap in a cancellable task.
                await self._cli_console.begin_turn(raw)
                execution = None
                self._turn_task = asyncio.create_task(self._run_turn(raw))
                # Same race guard as the slash-command path above.
                if getattr(self._cli_console, "_interrupt_requested", False):
                    self._turn_task.cancel()
                try:
                    execution = await self._turn_task
                except asyncio.CancelledError:
                    pass  # turn was interrupted; execution remains None
                finally:
                    # end_turn is called exactly once per input, regardless of outcome.
                    await self._cli_console.end_turn(execution)
                    self._turn_task = None
                if execution is not None:
                    try:
                        self._store.save_meta(self._session)
                    except Exception:
                        logger.exception("save_meta failed after turn")

        finally:
            # Both cleanup steps run independently so one failure doesn't block the other.
            # CancelledError is a BaseException; catch it explicitly at each step so a
            # second cancellation (e.g. from _force_exit()) cannot skip later cleanup.
            try:
                await self._cli_console.session_stop()
            except (asyncio.CancelledError, Exception):
                logger.exception("session_stop failed during cleanup")
            try:
                await self._cleanup_mcp_tools()
            except (asyncio.CancelledError, Exception):
                logger.exception("MCP cleanup failed during session teardown")

    async def submit_turn(self, user_input: str) -> None:
        """Entry point for RichTUIConsole (Phase 4) — called via callback."""
        await self._run_turn(user_input)
        try:
            self._store.save_meta(self._session)
        except Exception:
            logger.exception("save_meta failed after submit_turn")

    async def _run_turn(self, user_input: str) -> "AgentExecution | None":
        # begin_turn() / end_turn() are managed by the outer run() loop.
        self._last_user_input = user_input
        turn_index = self._store.next_turn_index(self._session.session_id)
        turn = SessionTurn(turn_index=turn_index, user_input=user_input)

        trajectory_path = self._store.make_turn_trajectory_path(
            self._session.session_id, turn.turn_index
        )
        patch_path = self._store.make_turn_patch_path(
            self._session.session_id, turn.turn_index
        )
        trajectory_path.parent.mkdir(parents=True, exist_ok=True)

        # Load accumulated full history and step_boundaries; empty on first turn.
        full_history = self._store.read_full_history(self._session.session_id)
        step_boundaries = self._store.read_step_boundaries(self._session.session_id)

        # Initialise to None so the outer finally can safely iterate even if
        # Agent() or initialize_subagent() raises before all three are created.
        turn_agent: Agent | None = None
        plan_agent: Agent | None = None
        test_agent: Agent | None = None

        # Outer finally: close() runs on ALL exit paths — normal return, Exception,
        # AND CancelledError — so executor threads are never left for GC.
        # Agent creation is inside the try so a construction failure also triggers
        # the finally and closes whichever wrappers were successfully created.
        execution = None
        try:
            turn_agent = Agent(
                "code_agent",
                self._config,
                str(trajectory_path),
                self._cli_console,
            )

            # Initialize subagents only when their config is present; tools that
            # require a missing subagent return an error from the tool itself.
            plan_agent = (
                Agent("plan_agent", self._config, turn_agent.trajectory_recorder, self._cli_console)
                if self._config.plan_agent is not None else None
            )
            test_agent = (
                Agent("test_agent", self._config, turn_agent.trajectory_recorder, self._cli_console)
                if self._config.test_agent is not None else None
            )
            turn_agent.agent.initialize_subagent(plan_agent, test_agent)

            task: dict[str, Any] = {
                "task_kind": "interactive_chat",
                "database": self._session.database,
                "func_name": user_input[:80],
                "directory": self._session.cwd,
                "user_input": user_input,
            }
            extra_args: dict[str, Any] = {
                "project_path": self._session.cwd,
                "must_patch": "false",
                "patch_path": str(patch_path),
                "chat_history_to_restore": full_history,
                "step_boundaries_to_restore": step_boundaries,
                "preserve_chat_history": True,
                "session_mcp_tools": self._mcp_tools,
                "skip_agent_mcp_bootstrap": True,
            }

            # pre_turn_boundary_idx: the index into _step_boundaries that holds the
            # "end of previous turn" boundary.  We read it AFTER the turn so that any
            # first-compaction +1 shift is already baked in.
            pre_turn_boundary_idx = len(step_boundaries) - 1  # -1 means "first turn, no prior"

            try:
                execution = await turn_agent.run(task, extra_args)
            except Exception as exc:
                # Single-turn failure does not terminate the session.
                logger.exception("Turn failed: %s", exc)
                self._cli_console.print(f"Turn failed: {exc}", color="red")
            # CancelledError is not caught here; it propagates to outer _turn_task
            # but still passes through the outer finally below.

            if execution is None:
                return None  # turn failed or was cancelled; skip history persistence

            # Persistence failures must not terminate the session: wrap in
            # try/except so a disk error only loses this turn's record.
            try:
                updated_history = [
                    _llm_message_to_dict(msg)
                    for msg in turn_agent.agent._agent_history
                ]
                post_boundaries = turn_agent.agent._step_boundaries
                self._store.write_full_history(self._session.session_id, updated_history)
                self._store.write_step_boundaries(self._session.session_id, post_boundaries)

                turn.finished_at = datetime.now().isoformat()
                turn.success = execution.success
                turn.trajectory_file = str(trajectory_path)
                turn.patch_file = str(patch_path)
                report_path = write_trajectory_report(trajectory_path)
                self._cli_console.turn_report_ready(report_path)

                # transcript.jsonl: human-readable record; used by Phase 3 /compact.
                self._store.append_transcript(
                    self._session.session_id,
                    TranscriptMessage(role="user", content=user_input, turn_index=turn.turn_index),
                )
                # Derive the (possibly compaction-shifted) start of this turn's new messages.
                # post_boundaries[pre_turn_boundary_idx] is the same logical boundary recorded
                # before the turn, already incremented by +1 if first-compaction fired this turn.
                # When pre_turn_boundary_idx == -1 (first turn ever), all messages are new → 0.
                if pre_turn_boundary_idx >= 0 and pre_turn_boundary_idx < len(post_boundaries):
                    history_start = post_boundaries[pre_turn_boundary_idx]
                else:
                    history_start = 0
                last_text = ""
                for msg in reversed(turn_agent.agent._agent_history[history_start:]):
                    if msg.role == "assistant" and (msg.content or "").strip():
                        last_text = msg.content.strip()
                        break
                self._store.append_transcript(
                    self._session.session_id,
                    TranscriptMessage(
                        role="assistant",
                        content=last_text,
                        turn_index=turn.turn_index,
                    ),
                )
                logger.debug("Turn report saved to: %s", report_path)
            except Exception:
                logger.exception("Post-turn persistence failed; turn result is preserved in memory")

            return execution
        finally:
            # Runs for every exit path including CancelledError (a BaseException).
            for _a in (turn_agent, plan_agent, test_agent):
                if _a is not None:
                    _a.close()

    async def _handle_command(self, raw: str) -> None:
        cmd, args = SlashCommandParser.parse(raw)

        if cmd == "/help":
            lines = ["Available commands:"]
            for name, desc in SLASH_COMMANDS.items():
                lines.append(f"  {name:15s} {desc}")
            self._cli_console.print("\n".join(lines))

        elif cmd == "/status":
            s = self._session
            self._cli_console.print(
                f"Session: {s.session_id}  title: {s.title or '(untitled)'}  cwd: {s.cwd}"
            )

        elif cmd == "/new":
            from code_agent.session.schema import SessionMeta as SM
            new_meta = SM(
                cwd=self._session.cwd,
                database=self._session.database,
                model=self._session.model,
            )
            self._session = self._store.create(new_meta)
            self._last_user_input = ""
            self._cli_console.print(f"New session started: {self._session.session_id}")
            self._cli_console.session_switch(self._session)

        elif cmd == "/resume":
            if not args:
                sessions = self._store.list()
                if not sessions:
                    self._cli_console.print("No sessions found.")
                    return
                for s in sessions[:5]:
                    self._cli_console.print(
                        f"  {s.session_id}  {s.updated_at[:19]}  {s.title or '(untitled)'}"
                    )
            else:
                meta = self._store.get(args[0])
                if meta is None:
                    # Global fallback: scan all project stores under ~/.opencook/sessions/
                    from pathlib import Path
                    sessions_root = Path.home() / ".opencook" / "sessions"
                    for proj_dir in sorted(sessions_root.iterdir()) if sessions_root.exists() else []:
                        if proj_dir.is_dir() and proj_dir != self._store._root:
                            from code_agent.session.store import SessionStore
                            candidate = SessionStore(root=proj_dir)
                            found = candidate.get(args[0])
                            if found is not None:
                                meta = (found, candidate)  # defer store swap until cwd check passes
                                break
                import os as _os
                if meta is None:
                    self._cli_console.print(f"Session {args[0]} not found.")
                else:
                    # Unpack deferred (meta, candidate_store) tuple from global search, or plain meta.
                    if isinstance(meta, tuple):
                        meta, candidate_store = meta
                    else:
                        candidate_store = None
                    if meta.cwd and not _os.path.isdir(meta.cwd):
                        self._cli_console.print(
                            f"Cannot resume session {args[0]}: "
                            f"working directory no longer exists ({meta.cwd})."
                        )
                    else:
                        if candidate_store is not None:
                            self._store = candidate_store
                        self._session = meta
                        self._last_user_input = ""
                        if meta.cwd:
                            _os.chdir(meta.cwd)
                        self._cli_console.print(f"Resumed session: {self._session.session_id}")
                        self._cli_console.session_switch(self._session)

        elif cmd == "/fork":
            new_meta = self._store.fork(self._session.session_id)
            self._session = new_meta
            self._last_user_input = ""
            self._cli_console.print(f"Forked to new session: {self._session.session_id}")
            self._cli_console.session_switch(self._session)

        elif cmd == "/rename":
            if args:
                self._session.title = " ".join(args)
                self._store.save_meta(self._session)
                self._cli_console.print(f"Session renamed to: {self._session.title}")
                self._cli_console.session_switch(self._session)
            else:
                self._cli_console.print("Usage: /rename <title>")

        elif cmd == "/plan":
            await self._run_plan_command()

        elif cmd == "/verify":
            await self._run_verify_command()

        elif cmd == "/clear":
            self._cli_console.terminal_clear()

        elif cmd == "/compact":
            self._cli_console.print("/compact is not yet implemented (Phase 3).")

        elif cmd == "/permissions":
            self._cli_console.print("/permissions is not yet implemented (Phase 2).")

        elif cmd == "/characterization":
            await self._run_characterization_command(args)

        else:
            self._cli_console.print(f"Unknown command: {cmd}. Type /help for available commands.")

    async def _run_characterization_command(self, args: list[str]) -> None:
        """Analyze working directory: file stats, function index, module dependencies.

        With a function name argument: show call-graph dependencies for that function.
        All heavy I/O runs in a thread to keep the event loop responsive.
        """
        import asyncio
        import inspect
        import os
        from collections import Counter

        from rich.table import Table
        from rich.text import Text

        from code_agent.tools.dep_tool import DepTool

        cwd = self._session.cwd or os.getcwd()
        typewriter_print = getattr(self._cli_console, "print_typewriter_async", None)
        animate_progress = getattr(self._cli_console, "animate_progress_async", None)
        set_live_status = getattr(self._cli_console, "_set_live_status", None)
        clear_live_status = getattr(self._cli_console, "clear_live_status", None)
        progress_frame_delay = getattr(self._cli_console, "_progress_frame_delay", 0.04)

        async def _run_with_progress(
            prefix: str,
            *,
            phrases: tuple[str, ...],
            status_label: str,
            work,
            shimmer_prefix: bool = False,
        ):
            if callable(clear_live_status):
                clear_live_status()
            elif callable(set_live_status):
                set_live_status(AgentStepState.CALLING_TOOL, status_label)
            elif hasattr(self._cli_console, "_spinning"):
                self._cli_console._spinning = False

            if callable(animate_progress):
                stop_event = asyncio.Event()
                animation_task = asyncio.create_task(
                    animate_progress(
                        prefix,
                        phrases=phrases,
                        color="blue",
                        bold=True,
                        delay=progress_frame_delay,
                        stop_event=stop_event,
                        final_suffix="complete",
                        shimmer_prefix=shimmer_prefix,
                    )
                )
                try:
                    return await asyncio.to_thread(work)
                finally:
                    stop_event.set()
                    try:
                        await animation_task
                    except Exception:
                        pass

            if callable(typewriter_print):
                maybe_awaitable = typewriter_print(prefix, color="blue", bold=True, delay=0.01)
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
            else:
                self._cli_console.print(prefix, color="blue", bold=True)
            return await asyncio.to_thread(work)

        # ── Function dependency mode (fast — no directory walk) ───────────
        if args:
            func_name = args[0]

            def _func_deps():
                tool = DepTool(project_root=cwd)
                decl = tool.get_function_declaration(func_name)
                deps = tool.get_function_dependencies(func_name, is_pruned=True)
                return decl, deps

            decl, deps = await _run_with_progress(
                f"Indexing {func_name}...",
                phrases=("resolving declaration", "walking call edges", "collecting dependencies"),
                status_label=f"indexing {func_name}",
                work=_func_deps,
                shimmer_prefix=False,
            )

            t = Table(title=f"Function: {func_name}", show_header=True, header_style="bold cyan",
                      border_style="bright_black", show_lines=False, expand=False)
            t.add_column("Kind", style="cyan", no_wrap=True)
            t.add_column("Detail")
            if decl:
                t.add_row("declaration", decl)
            if not deps:
                t.add_row("deps", "(none found)")
            else:
                for kind, items in deps.items():
                    for item in items:
                        t.add_row(kind, item)
            self._cli_console.write_rich(t)
            return

        # ── Directory scan (potentially slow — run in thread) ─────────────
        _TEXT_EXTS = {
            ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp",
            ".py", ".js", ".ts", ".go", ".rs", ".java",
            ".md", ".txt", ".sql", ".sh", ".yaml", ".yml",
            ".json", ".toml", ".ini", ".cfg", ".tcl", ".lua",
        }

        def _render_scan_report(
            ext_counter: Counter[str],
            total_lines: int,
            func_count: int,
            unique_names: int,
            file_func_counter: Counter[str],
            py_imports: Counter[str],
        ) -> None:
            # TEMP(demo): keep the production Rich rendering path intact so the
            # replayed snapshot looks identical during recording.
            files_tbl = Table(title=f"[bold]{os.path.basename(cwd)}[/bold] — file breakdown",
                              show_header=True, header_style="bold cyan",
                              border_style="bright_black", show_lines=False, expand=False)
            files_tbl.add_column("Extension", style="cyan", no_wrap=True, min_width=10)
            files_tbl.add_column("Files", justify="right", style="white")
            for ext, cnt in ext_counter.most_common(12):
                files_tbl.add_row(ext, str(cnt))
            files_tbl.add_section()
            files_tbl.add_row("[dim]total files[/dim]", str(sum(ext_counter.values())))
            files_tbl.add_row("[dim]text lines[/dim]", f"{total_lines:,}")

            func_hdr = Text()
            func_hdr.append("  Functions: ", style="bold cyan")
            func_hdr.append(f"{func_count:,}", style="bold white")
            func_hdr.append(" across ", style="dim")
            func_hdr.append(f"{unique_names:,}", style="bold white")
            func_hdr.append(" unique names", style="dim")
            self._cli_console.write_rich(func_hdr)
            self._cli_console.write_rich(Text(" "))

            top_tbl = Table(title="Top files by function count",
                            show_header=True, header_style="bold cyan",
                            border_style="bright_black", show_lines=False, expand=False)
            top_tbl.add_column("#", justify="right", style="yellow", width=5)
            top_tbl.add_column("File", style="white")
            for fpath, cnt in file_func_counter.most_common(10):
                top_tbl.add_row(str(cnt), fpath)

            from rich.columns import Columns
            self._cli_console.write_rich(
                Columns([files_tbl, top_tbl], equal=False, expand=False, padding=(0, 4))
            )

            if py_imports:
                imp_tbl = Table(title="Top Python imports",
                                show_header=True, header_style="bold cyan",
                                border_style="bright_black", show_lines=False, expand=False)
                imp_tbl.add_column("Module", style="cyan")
                imp_tbl.add_column("Files", justify="right", style="white")
                for mod, cnt in py_imports.most_common(12):
                    imp_tbl.add_row(mod, str(cnt))
                self._cli_console.write_rich(imp_tbl)

        def _scan():
            tool = DepTool(project_root=cwd)
            ext_counter: Counter[str] = Counter()
            total_lines = 0
            for dirpath, dirnames, filenames in os.walk(cwd):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower() or "(no ext)"
                    ext_counter[ext] += 1
                    if ext in _TEXT_EXTS:
                        fpath = os.path.join(dirpath, fname)
                        try:
                            with open(fpath, encoding="utf-8", errors="ignore") as f:
                                total_lines += sum(1 for _ in f)
                        except OSError:
                            pass

            index = tool._get_index()
            func_count = sum(len(v) for v in index.values())
            file_func_counter: Counter[str] = Counter()
            for entries in index.values():
                for e in entries:
                    rel = os.path.relpath(e["file"], cwd)
                    file_func_counter[rel] += 1

            py_imports: Counter[str] = Counter()
            try:
                for dirpath, dirnames, filenames in os.walk(cwd):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for fname in filenames:
                        if not fname.endswith(".py"):
                            continue
                        fpath = os.path.join(dirpath, fname)
                        try:
                            src = open(fpath, encoding="utf-8", errors="ignore").read()
                            for imp in tool._find_py_imports(src):
                                py_imports[imp.split(".")[0]] += 1
                        except OSError:
                            pass
            except Exception:
                pass

            return ext_counter, total_lines, func_count, len(index), file_func_counter, py_imports

        scan_work = _scan

        (ext_counter, total_lines, func_count, unique_names,
         file_func_counter, py_imports) = await _run_with_progress(
            f"Scanning {cwd} ...",
            phrases=(
                "counting files",
                "measuring text lines",
                "reading function index",
                "ranking busy files",
                "tracing python imports",
            ),
            status_label="scanning workspace",
            work=scan_work,
            shimmer_prefix=True,
        )

        _render_scan_report(
            ext_counter,
            total_lines,
            func_count,
            unique_names,
            file_func_counter,
            py_imports,
        )
        return

    async def _run_plan_command(self) -> None:
        """Run plan_agent once for the current session context.

        Result is printed to the console but NOT written to session history or transcript,
        so it does not pollute the main conversation context.
        """
        if self._config.plan_agent is None:
            self._cli_console.print("plan_agent is not configured.")
            return
        # Build user_input from recent transcript so plan_agent has session context,
        # not just the last single message.  Limit to 5 entries to stay within token budget.
        recent = self._store.read_transcript(self._session.session_id)[-5:]
        if recent:
            context = "\n".join(f"{m.role}: {m.content[:300]}" for m in recent)
            user_input = (
                f"Recent conversation context:\n{context}\n\n"
                f"Based on the above, create an implementation plan."
            )
        else:
            user_input = self._last_user_input or "Analyze the current state and create a plan."
        task = {
            "task_kind": "interactive_chat",
            "database": self._session.database,
            "directory": self._session.cwd,
            "user_input": user_input,
        }
        extra_args = {"project_path": self._session.cwd}
        await self._cli_console.begin_subagent_run()
        plan_agent = Agent("plan_agent", self._config, cli_console=self._cli_console)
        try:
            plan_str = await plan_agent.agent.get_processed_plan(task, extra_args)
        finally:
            plan_agent.close()
        self._cli_console.print(f"\n[Plan]\n{plan_str or '(No plan generated.)'}")

    async def _run_verify_command(self) -> None:
        """Run test_agent verification once for the current session context.

        Result is printed to the console but NOT written to session history or transcript.
        """
        if self._config.test_agent is None:
            self._cli_console.print("test_agent is not configured.")
            return
        task = {
            "task_kind": "interactive_chat",
            "database": self._session.database,
            "directory": self._session.cwd,
            "user_input": self._last_user_input or "Verify the current implementation.",
        }
        extra_args = {"project_path": self._session.cwd}
        await self._cli_console.begin_subagent_run()
        test_agent = Agent("test_agent", self._config, cli_console=self._cli_console)
        try:
            result = await test_agent.agent.run_verification(task, extra_args)
        finally:
            test_agent.close()
        output = result.output or result.error or "(No verification result.)"
        self._cli_console.print(f"\n[Verification]\n{output}")

    async def _init_mcp_tools(self) -> list:
        """Initialise MCP tools once for the whole session."""
        try:
            # Reuse the agent's initialise_mcp path via a temporary agent instance.
            # We keep a reference to the underlying CodeAgent so its mcp_clients list
            # can be properly cleaned up via cleanup_mcp_clients() at session end.
            temp_agent = Agent("code_agent", self._config)
            try:
                await temp_agent.agent.initialise_mcp()
                tools = list(temp_agent.agent._tools)
                # Only return MCP tools (those not in the default tool registry)
                from code_agent.tools import tools_registry
                mcp_tools = [t for t in tools if t.get_name() not in tools_registry]
                self._mcp_agent = temp_agent.agent  # retain for MCP cleanup at session end
                return mcp_tools
            finally:
                # LLM executor is not needed after MCP init; close on both success
                # and failure paths so the thread is not left for GC.
                temp_agent.close()
        except Exception:
            return []

    async def _cleanup_mcp_tools(self) -> None:
        """Clean up MCP connections at session end via the owning CodeAgent."""
        try:
            if self._mcp_agent is not None:
                await self._mcp_agent.cleanup_mcp_clients()
                self._mcp_agent = None
        except (asyncio.CancelledError, Exception):
            # CancelledError is a BaseException; catch it explicitly so MCP
            # cleanup is not silently skipped when the runner task is cancelled
            # (e.g. via _force_exit() in TextualConsole).
            pass
