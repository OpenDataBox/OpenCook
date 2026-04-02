# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Working memory manager and offload compactor (Layer A).

Two compaction modes:
  - Bulk compaction: triggered by token-ratio threshold; archives all non-protected
    tool_result messages outside the recent-N-steps guard window, then injects a
    snapshot with a progress summary and usage guide.
  - Eager spike offload: triggered immediately when a single tool_result exceeds
    compaction_spike_tokens; does not wait for bulk triggers.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from code_agent.memory.schema import CompactStub
from code_agent.utils.llm_clients.llm_basics import LLMMessage

# Tool results from these tools are never archived — they are critical task evidence
PROTECT_TOOL_NAMES = {"test_subagent", "task_done", "plan_subagent"}

# Prefix used by CompactStub.to_text(); used to detect already-compacted messages
_STUB_PREFIX = "[COMPACTED at step "

# Prefix prepended to spike-truncated messages so bulk compaction skips them.
# Full content is already on disk from the spike pass; re-archiving the truncated
# copy would break the reference chain (stub would point to a secondary file).
_SPIKE_PREFIX = "[SPIKE-TRUNCATED at step "

# Characters of the original content kept inline after spike offload.
# Preserves the most important part of large results (errors, first output lines)
# so the model and the bulk-compaction summarizer both have meaningful context.
_SPIKE_INLINE_CHARS = 800

# Prefix of the working-memory snapshot message injected during bulk compaction
_COMPACT_SNAPSHOT_PREFIX = "[COMPACTION SNAPSHOT"

# Rough approximation: 1 token ≈ 4 characters (ASCII-heavy code / text)
_CHARS_PER_TOKEN = 4

# Number of leading messages (system + initial user) always protected from compaction
_ALWAYS_PROTECT_LEADING = 2

# Maximum length of call_id suffix used in archive filenames (truncated for safety)
_CALL_ID_SUFFIX_LEN = 16


class WorkingMemoryManager:
    """Manages Layer A compaction and the compact/ archive directory on disk."""

    def __init__(self, session_id: str, config, model_context_window: int = 128_000):
        self._config = config
        self._context_window = model_context_window
        self._compact_dir = (
            Path(tempfile.gettempdir()) / "dbcooker_sessions" / session_id / "compact"
        )
        self._compact_dir.mkdir(parents=True, exist_ok=True)

    def should_compact(self, messages: list[LLMMessage]) -> bool:
        """Return True when bulk compaction should be triggered."""
        if not self._config.compaction_enabled:
            return False
        estimated_tokens = sum(
            len(msg.content or "")
            + len(str(getattr(getattr(msg, "tool_call", None), "arguments", "") or ""))
            + len(getattr(getattr(msg, "tool_result", None), "result", "") or "")
            for msg in messages
        ) // _CHARS_PER_TOKEN
        return estimated_tokens / self._context_window > self._config.compaction_token_ratio

    def _build_compaction_snapshot(
        self,
        step_number: int,
        newly_archived: list[tuple[str, int, str]],
        summary: str | None = None,
    ) -> str:
        """
        Build the compaction snapshot injected after bulk archiving.

        summary: optional LLM-generated narrative of the compacted region.
        When None, the snapshot contains only the usage guide and archived file list.
        Always emits the grep usage guide once so it is not repeated in every stub.
        """
        lines = [f"{_COMPACT_SNAPSHOT_PREFIX} — step {step_number}]"]

        # LLM-generated summary takes the first slot — richer semantic narrative.
        if summary:
            lines += ["", "## Progress Summary", summary]

        # Always include the usage guide — stubs only carry minimal info (tool + path).
        lines += [
            "",
            "## Archived Tool Results",
            "Tool results marked [COMPACTED] have been offloaded to disk.",
            "Each stub shows: tool name | approximate size | archive path.",
            "To read a specific section:  bash> grep -n \"<keyword>\" \"<archive_path>\"",
            "Do NOT cat archived files — they may be thousands of tokens.",
        ]
        if newly_archived:
            lines.append("Files archived in this pass:")
            for tool_name, tokens, path in newly_archived:
                lines.append(f"  - {tool_name} (~{tokens} tokens): {path}")

        return "\n".join(lines)

    def compact(
        self,
        messages: list[LLMMessage],
        step_number: int,
        step_boundaries: list[int],
        summarizer=None,
    ) -> tuple[list[LLMMessage], list[int]]:
        """
        Bulk compaction: archive non-protected tool_result messages outside the guard
        window, then inject a working-memory snapshot with a shared usage guide.

        summarizer: optional callable (list[LLMMessage]) -> str supplied by MemoryManager
        when an llm_client is available.  It receives the messages about to be archived
        and returns a free-form narrative summary.  When None, the snapshot contains
        only the usage guide and the list of newly archived files.  The summarizer is
        called AFTER the compaction loop (once we know archiving actually happened) and
        receives the original pre-stub messages.

        Double-compaction prevention:
          - Already-stubbed tool_results are detected by _STUB_PREFIX and skipped.
          - The snapshot is a content (non-tool_result) message and is therefore never
            eligible for compaction in the tool_result processing loop.

        Snapshot injection changes message count on first compaction only:
          - First compaction: snapshot inserted at index _ALWAYS_PROTECT_LEADING;
            len grows by 1 and all boundaries >= _ALWAYS_PROTECT_LEADING shift by +1.
          - Subsequent compactions: existing snapshot replaced in-place;
            len and boundaries are unchanged.

        step_boundaries must be initialised as [0] (sentinel) before the loop starts so
        that step_boundaries[-(protect_steps+1)] correctly identifies the guard window
        start even when step count equals protect_steps+1.
        """
        protect_steps = self._config.compaction_protect_recent_steps
        # step_boundaries = [0, end_step1, ..., end_stepN]  (N+1 elements for N steps)
        # protect_from = boundaries[-(protect_steps+1)] = start of the last protect_steps steps
        if len(step_boundaries) <= protect_steps + 1:
            return messages, step_boundaries  # not enough history to compact

        protect_from = step_boundaries[-(protect_steps + 1)]

        new_messages: list[LLMMessage] = []
        newly_archived: list[tuple[str, int, str]] = []  # (tool_name, tokens, archive_path)
        for i, msg in enumerate(messages):
            # Always protect: leading messages (system + initial user) and guard window.
            if i < _ALWAYS_PROTECT_LEADING or i >= protect_from:
                new_messages.append(msg)
                continue
            # Non-tool_result messages (assistant text, snapshot content) pass through
            # unchanged.  This is also what prevents the snapshot from being double-compacted.
            if msg.tool_result is None:
                new_messages.append(msg)
                continue
            if msg.tool_result.name in PROTECT_TOOL_NAMES:
                new_messages.append(msg)
                continue

            content = msg.tool_result.result or ""
            if len(content) // _CHARS_PER_TOKEN == 0:
                new_messages.append(msg)
                continue
            # Already processed — skip to prevent double-compaction.
            # _STUB_PREFIX: fully replaced by a prior bulk compaction pass.
            # _SPIKE_PREFIX: truncated by spike offload; full content already on disk.
            if content.startswith(_STUB_PREFIX) or content.startswith(_SPIKE_PREFIX):
                new_messages.append(msg)
                continue

            archive_path = self._archive(
                content=content,
                tool_name=msg.tool_result.name,
                call_id=msg.tool_result.call_id,
                step_number=step_number,
            )
            tokens = len(content) // _CHARS_PER_TOKEN
            newly_archived.append((msg.tool_result.name, tokens, str(archive_path)))

            stub = CompactStub(
                archive_path=str(archive_path),
                tool_name=msg.tool_result.name,
                original_tokens=tokens,
                step_number=step_number,
                call_id=msg.tool_result.call_id,
            )
            new_msg = LLMMessage(role="user")
            new_msg.tool_result = type(msg.tool_result)(
                call_id=msg.tool_result.call_id,
                name=msg.tool_result.name,
                success=msg.tool_result.success,
                result=stub.to_text(),
                error=msg.tool_result.error,
            )
            new_messages.append(new_msg)

        # If nothing was actually archived, the loop found no eligible messages —
        # skip the summarizer call and snapshot injection entirely.
        if not newly_archived:
            return messages, step_boundaries

        # Summarizer is called AFTER the loop: we now know archiving actually happened,
        # avoiding a wasted LLM call when newly_archived is empty.
        # `messages` (not new_messages) is passed so the summarizer sees original
        # content — the loop builds a separate new_messages list and never mutates messages.
        summary: str | None = None
        if summarizer is not None:
            msgs_to_summarize = messages[_ALWAYS_PROTECT_LEADING:protect_from]
            if msgs_to_summarize:
                summary = summarizer(msgs_to_summarize)

        # Inject (or replace) a snapshot right after the protected leading messages.
        # The snapshot provides semantic context (plan/test/files) and the shared grep
        # usage guide once, avoiding repetition across every individual stub.
        snapshot_text = self._build_compaction_snapshot(step_number, newly_archived, summary)
        snapshot_msg = LLMMessage(role="user", content=snapshot_text)
        existing_snapshot = (
            len(messages) > _ALWAYS_PROTECT_LEADING
            and messages[_ALWAYS_PROTECT_LEADING].tool_result is None
            and (messages[_ALWAYS_PROTECT_LEADING].content or "").startswith(
                _COMPACT_SNAPSHOT_PREFIX
            )
        )
        if existing_snapshot:
            # Replace in-place — message count and boundaries are unchanged.
            new_messages[_ALWAYS_PROTECT_LEADING] = snapshot_msg
            return new_messages, step_boundaries
        else:
            # First compaction: insert new message, shift all affected boundaries by +1.
            new_messages.insert(_ALWAYS_PROTECT_LEADING, snapshot_msg)
            new_boundaries = [
                b + (1 if b >= _ALWAYS_PROTECT_LEADING else 0)
                for b in step_boundaries
            ]
            return new_messages, new_boundaries

    def offload_spike(self, msg: LLMMessage, step_number: int) -> LLMMessage:
        """
        Eager single-message offload: immediately archive a tool_result that exceeds
        compaction_spike_tokens.  Called per-message in _tool_call_handler without
        waiting for ratio triggers.  Returns the (possibly replaced) message.
        """
        if msg.tool_result is None:
            return msg
        if msg.tool_result.name in PROTECT_TOOL_NAMES:
            return msg
        content = msg.tool_result.result or ""
        # Already a stub — skip to prevent double-compaction.
        if content.startswith(_STUB_PREFIX):
            return msg
        est_tokens = len(content) // _CHARS_PER_TOKEN
        if est_tokens < self._config.compaction_spike_tokens:
            return msg

        archive_path = self._archive(
            content=content,
            tool_name=msg.tool_result.name,
            call_id=msg.tool_result.call_id,
            step_number=step_number,
        )
        # Truncate rather than fully replace: keep the first _SPIKE_INLINE_CHARS
        # characters so the model and the bulk-compaction summarizer both see
        # meaningful content (errors, first output lines) in context immediately.
        # _SPIKE_PREFIX is prepended so bulk compaction skips this message —
        # the full content is already on disk; re-archiving the truncated copy
        # would break the reference chain (stub would point to a secondary file).
        truncated = (
            f"{_SPIKE_PREFIX}{step_number}]\n"
            + content[:_SPIKE_INLINE_CHARS]
            + f"\n… [OUTPUT TRUNCATED — ~{est_tokens} tokens total."
            + f" Full content: {archive_path}]"
        )
        new_msg = LLMMessage(role="user")
        new_msg.tool_result = type(msg.tool_result)(
            call_id=msg.tool_result.call_id,
            name=msg.tool_result.name,
            success=msg.tool_result.success,
            result=truncated,
            error=msg.tool_result.error,
        )
        return new_msg

    def _archive(self, content: str, tool_name: str, call_id: str, step_number: int) -> Path:
        """Write content to a plain-text archive file and return the path."""
        safe_call_id = (call_id or "")[:_CALL_ID_SUFFIX_LEN].replace("/", "_")
        filename = f"step_{step_number:03d}_{tool_name}_{safe_call_id}.txt"
        path = self._compact_dir / filename
        path.write_text(content, encoding="utf-8")
        return path
