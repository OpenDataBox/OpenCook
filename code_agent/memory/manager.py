# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""MemoryManager: unified public interface for the DBCooker memory system.

Owns all four memory layers and is the single dependency injected into CodeAgent
and BaseAgent.  Callers interact only with this class; they never import the
individual layer modules directly.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def _project_key(project_root: Path) -> str:
    """Return a human-readable, collision-resistant subdirectory name for a project.

    Format: ``{basename}_{8-char hex}`` — e.g. ``sqlite_a3f4b2c1``.
    The basename makes the directory recognisable; the hash disambiguates
    projects that share the same directory name but live at different paths.
    """
    short_hash = hashlib.sha256(str(project_root).encode()).hexdigest()[:8]
    return f"{project_root.name}_{short_hash}"

from code_agent.memory.config import MemoryConfig
from code_agent.memory.episodic import EpisodicMemory
from code_agent.memory.project import ProjectMemory
from code_agent.memory.working import WorkingMemoryManager
from code_agent.memory import render
from code_agent.utils.llm_clients.llm_basics import LLMMessage

# Maximum characters of a single tool result included in the summarization input.
# Keeps the summary LLM call small; spike-offloaded stubs are already single-line.
_SUMMARY_CONTENT_MAX_CHARS = 2000

# Hard cap on the generated summary length (≈200 words × 6 chars/word).
# If the model exceeds this, the output is truncated with a marker.
_SUMMARY_MAX_CHARS = 1200

# Prompt sent to the LLM when summarizing the compacted region.
_SUMMARIZATION_PROMPT = """\
The conversation history above is about to be archived to free up context space.
Write a concise Progress Summary (≤200 words) covering:
1. What was discovered or learned from file reads, bash outputs, and tool results
2. What code changes were made and their current status
3. Any constraints, patterns, or pitfalls found along the way
4. What still needs to be done

Be specific — reference file names, function names, and line numbers where relevant.
Output only the summary text itself, no preamble or closing remarks."""

# Maximum number of "Where to Work" bullets extracted per trajectory
_HINT_WHERE_MAX = 10

# Maximum number of "How to Check" bullets extracted per trajectory
_HINT_HOW_MAX = 5

# Maximum number of "Watch Outs" bullets extracted per trajectory
_HINT_WATCH_MAX = 8

# File tools whose path arguments are harvested for "Where to Work" hints
_FILE_TOOLS = {
    "str_replace_based_edit_tool",  # arg: path
    "json_edit_tool",               # arg: file_path
    "understand_toolkit",           # args: file_path (str), file_paths (comma-separated)
}

# Regex matching test-runner commands for "How to Check" hints
_TEST_CMD_RE = re.compile(r"pytest|python\s+-m\s+pytest|cargo\s+test|go\s+test|make\s+test")

# Prefixes that identify error lines in tool_result output
_ERROR_SIGNALS = ("Error:", "Exception:", "Traceback (most", "FAILED", "assert")


class MemoryManager:
    """
    Unified entry point for all memory operations.

    Lifecycle per task:
      new_task()     → build_context()   → inject into prompts
      execute_task() → write_episode()   → append to Layer B
                     → write_candidate() → append to Layer C candidates
    Layer A (compaction) is activated separately via init_working() in new_task()
    and queried from BaseAgent's execute loop.
    """

    def __init__(self, config: MemoryConfig, project_path: str):
        self._config = config
        self._project_root = Path(project_path).resolve()
        memory_root = Path(config.memory_root).expanduser()
        # Partition memory by project so different repositories do not share records.
        memory_root = memory_root / _project_key(self._project_root)
        self._episodic_memory = EpisodicMemory(memory_root)
        self._project_memory = ProjectMemory(memory_root)
        self._working: WorkingMemoryManager | None = None

    # ------------------------------------------------------------------
    # Layer A — working memory / compaction
    # ------------------------------------------------------------------

    def init_working(self, session_id: str, model_context_window: int = 128_000) -> None:
        """Initialise Layer A at the start of new_task() (creates compact/ dir and state)."""
        self._working = WorkingMemoryManager(
            session_id=session_id,
            config=self._config,
            model_context_window=model_context_window,
        )

    def should_compact(self, messages: list[LLMMessage]) -> bool:
        if not self._working or not self._config.compaction_enabled:
            return False
        return self._working.should_compact(messages)

    def compact(
        self,
        messages: list[LLMMessage],
        step_number: int,
        step_boundaries: list[int],
        llm_client=None,
        model_config=None,
    ) -> tuple[list[LLMMessage], list[int]]:
        if not self._working:
            return messages, step_boundaries
        summarizer = None
        if llm_client is not None and model_config is not None:
            def summarizer(msgs: list[LLMMessage]) -> str:
                return MemoryManager._call_summarizer(msgs, llm_client, model_config)
        return self._working.compact(
            messages, step_number, step_boundaries, summarizer
        )

    @staticmethod
    def _format_messages_for_summary(messages: list[LLMMessage]) -> str:
        """
        Flatten a slice of _agent_history into readable text for the summarization prompt.
        Spike-offloaded stubs (single-line [COMPACTED ...] text) are included as-is so
        the model knows those results exist even though their full content is archived.
        """
        parts = []
        for msg in messages:
            if msg.role == "assistant":
                if msg.tool_call is not None:
                    args = str(getattr(msg.tool_call, "arguments", "") or "")[:300]
                    parts.append(f"[assistant → {msg.tool_call.name}({args})]")
                elif msg.content:
                    parts.append(f"[assistant]: {msg.content[:500]}")
            elif msg.role == "user":
                if msg.tool_result is not None:
                    content = msg.tool_result.result or ""
                    if len(content) > _SUMMARY_CONTENT_MAX_CHARS:
                        content = content[:_SUMMARY_CONTENT_MAX_CHARS] + " … (truncated)"
                    parts.append(f"[{msg.tool_result.name} result]: {content}")
                elif msg.content:
                    parts.append(f"[user]: {msg.content[:300]}")
        return "\n".join(parts)

    @staticmethod
    def _call_summarizer(messages: list[LLMMessage], llm_client, model_config) -> str:
        """
        Call the LLM with the formatted history slice + summarization prompt.
        Returns the summary string, or "" on failure.

        Isolation guarantees:
          - reuse_history=False: the call is based solely on the formatted archive
            text, not the current provider conversation history.
          - trajectory_recorder temporarily disabled: the internal summarization
            call must not appear in the task trajectory or token statistics.
        Both are restored unconditionally in the finally block.
        """
        formatted = MemoryManager._format_messages_for_summary(messages)
        if not formatted.strip():
            return ""
        prompt = (
            f"<archived_history>\n{formatted}\n</archived_history>\n\n"
            f"{_SUMMARIZATION_PROMPT}"
        )
        # Save recorder from the underlying provider (LLMClient wraps self.client;
        # BaseLLMClient subclasses hold trajectory_recorder directly).
        underlying = getattr(llm_client, "client", llm_client)
        recorder = getattr(underlying, "trajectory_recorder", None)
        try:
            llm_client.set_trajectory_recorder(None)
            response = llm_client.chat(
                [LLMMessage(role="user", content=prompt)],
                model_config,
                tools=None,
                reuse_history=False,
            )
            text = (response.content or "").strip()
            # LLMClient.chat() swallows provider exceptions and returns
            # "[LLM ERROR] ..." instead of raising — treat this as failure.
            if not text or text.startswith("[LLM ERROR]"):
                print(f"[MemoryManager] Summarization LLM call failed: {text}")
                return ""
            if len(text) > _SUMMARY_MAX_CHARS:
                text = text[:_SUMMARY_MAX_CHARS] + " … (truncated)"
            return text
        except Exception as e:
            print(f"[MemoryManager] Summarization LLM call failed: {e}")
            return ""
        finally:
            llm_client.set_trajectory_recorder(recorder)

    def offload_spike(self, msg: LLMMessage, step_number: int) -> LLMMessage:
        """Eagerly offload a single oversized tool_result; called per-message in _tool_call_handler."""
        if not self._working:
            return msg
        return self._working.offload_spike(msg, step_number)

    # ------------------------------------------------------------------
    # Layer B + C — context injection
    # ------------------------------------------------------------------

    def build_context(self, task: dict, scopes: list[str]) -> dict:
        """
        Read Layer B + C memory and return prompt injection content.
        Called once at the start of new_task(); result is cached by the caller.

        ensure_scope_file() must run before read_wisdom_merged() so that the
        template file is created on first run and the system_addon is non-empty.
        """
        for scope in scopes:
            self._project_memory.ensure_scope_file(scope)
        wisdom = self._project_memory.read_wisdom_merged(scopes)

        episodes = self._episodic_memory.query(
            category=task.get("category", ""),
            database=task.get("database", ""),
            top_k=self._config.episode_inject_top_k,
        )
        return {
            "system_addon": render.build_system_addon(scopes, wisdom),
            "user_addon": render.build_user_addon(episodes),
        }

    # ------------------------------------------------------------------
    # Layer B — episode write
    # ------------------------------------------------------------------

    def write_episode(self, trajectory_data: dict, trajectory_path: str) -> None:
        """Append a per-task EpisodeRecord to the Layer B JSONL index after execute_task()."""
        if self._config.session_write_enabled:
            self._episodic_memory.write_episode(trajectory_data, trajectory_path)

    # ------------------------------------------------------------------
    # Layer C — candidate write
    # ------------------------------------------------------------------

    def write_candidate(self, trajectory_data: dict, scopes: list[str]) -> None:
        """
        Extract heuristic hints from trajectory_data and append them as per-bullet
        candidate records to each DB-specific scope's candidates file.

        Only DB-specific scopes are written; 'general' is excluded because general.md
        is populated exclusively by the offline consolidate_general() process that
        aggregates cross-scope evidence.
        """
        if not self._config.candidate_write_enabled:
            return
        db_scopes = [s for s in scopes if s != "general"]
        if not db_scopes:
            return

        task = trajectory_data.get("task", {})
        steps = trajectory_data.get("agent_steps", [])
        bullet_records = self._extract_hints(
            steps=steps,
            success=trajectory_data.get("success", False),
            source_task=task.get("func_name", ""),
            start_time=trajectory_data.get("start_time", ""),
            project_root=self._project_root,
        )
        for scope in db_scopes:
            for record in bullet_records:
                self._project_memory.write_candidate(scope, record)

    def consolidate(self, scopes: list[str]) -> None:
        """
        Promote high-frequency candidates into official wisdom. Call after write_candidate().

        Per-DB: runs consolidate(scope) for each DB-specific scope in scopes.
        General: runs consolidate_general() with db_scopes=None so it checks all
        known DB candidates — not just the current task's DB — enabling cross-DB
        bullets to be promoted as soon as enough evidence accumulates across tasks.

        No-op when candidate_write_enabled is False.
        """
        if not self._config.candidate_write_enabled:
            return
        db_scopes = [s for s in scopes if s != "general"]
        for scope in db_scopes:
            self._project_memory.consolidate(scope)
        self._project_memory.consolidate_general()

    # ------------------------------------------------------------------
    # Heuristic hint extractor (no LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_hints(
        steps: list[dict],
        success: bool,
        source_task: str,
        start_time: str,
        project_root: Path,
    ) -> list[dict]:
        """
        Heuristically extract wisdom hints from trajectory agent_steps without calling an LLM.

        Returns a list of per-bullet candidate records, each with the format:
          {section, bullet, source_task, success, start_time}

        The same format is used for LLM-generated candidates so both flow through the
        same consolidate() gate logic.

        tool_call schema (from TrajectoryRecorder):
          {call_id, name: str, arguments: dict, id}
        tool_result schema:
          {call_id, success: bool, result: str, error: str|None, id}
        """

        def _try_rel(path_str: str) -> str | None:
            """Convert to project-relative POSIX path, or None if outside the project root."""
            try:
                return str(
                    Path(path_str).resolve().relative_to(project_root)
                ).replace("\\", "/")
            except (ValueError, OSError):
                return None

        seen_paths: set[str] = set()
        seen_cmds: set[str] = set()
        seen_errors: set[str] = set()

        where_to_work: list[str] = []
        how_to_check: list[str] = []
        watch_outs: list[str] = []

        for step in steps:
            tool_calls = step.get("tool_calls") or []
            tool_results = step.get("tool_results") or []

            for call in tool_calls:
                tool_name = call.get("name", "")
                arguments = call.get("arguments") or {}

                # Where to Work: paths touched by file-editing tools
                if tool_name in _FILE_TOOLS:
                    raw_paths: list[str] = []
                    for key in ("path", "file_path", "filepath"):
                        v = arguments.get(key)
                        if isinstance(v, str) and v:
                            raw_paths.append(v)
                            break
                    # understand_toolkit passes file_paths as a comma-separated string
                    file_paths_arg = arguments.get("file_paths")
                    if isinstance(file_paths_arg, str) and file_paths_arg:
                        raw_paths.extend(
                            p.strip() for p in file_paths_arg.split(",") if p.strip()
                        )
                    for path in raw_paths:
                        rel = _try_rel(path)
                        if rel is None:
                            continue
                        bullet = f"- {rel}"
                        if bullet not in seen_paths:
                            seen_paths.add(bullet)
                            where_to_work.append(bullet)

                # How to Check: bash commands containing test-runner keywords
                if tool_name in {"bash", "run_bash", "execute_bash"}:
                    cmd = (arguments.get("command") or "").strip()
                    if _TEST_CMD_RE.search(cmd):
                        bullet = f"- `{cmd[:120]}`"
                        if bullet not in seen_cmds:
                            seen_cmds.add(bullet)
                            how_to_check.append(bullet)

            # Watch Outs: first informative error line from failed tool results
            for result in tool_results:
                if result.get("success", True):
                    continue
                raw = result.get("error") or str(result.get("result") or "")
                first_line = next(
                    (
                        line.strip()
                        for line in raw.splitlines()
                        if line.strip() and any(
                            line.strip().startswith(sig) for sig in _ERROR_SIGNALS
                        )
                    ),
                    None,
                )
                if not first_line or len(first_line) > 100:
                    continue
                bullet = f"- {first_line}"
                if bullet not in seen_errors:
                    seen_errors.add(bullet)
                    watch_outs.append(bullet)

        meta = {"source_task": source_task, "success": success, "start_time": start_time}
        records: list[dict] = []
        for bullet in where_to_work[:_HINT_WHERE_MAX]:
            records.append({"section": "Where to Work", "bullet": bullet, **meta})
        for bullet in how_to_check[:_HINT_HOW_MAX]:
            records.append({"section": "How to Check", "bullet": bullet, **meta})
        for bullet in watch_outs[:_HINT_WATCH_MAX]:
            records.append({"section": "Watch Outs", "bullet": bullet, **meta})
        return records
