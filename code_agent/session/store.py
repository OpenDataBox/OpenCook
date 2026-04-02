# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Session persistence layer."""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from code_agent.session.schema import SessionMeta, SessionTurn, TranscriptMessage
from code_agent.tools.base import ToolCall, ToolResult
from code_agent.utils.llm_clients.llm_basics import LLMMessage


def _llm_message_to_dict(msg: LLMMessage) -> dict:
    """Serialize LLMMessage (with nested ToolCall/ToolResult) to a plain dict."""
    return dataclasses.asdict(msg)


def _dict_to_llm_message(d: dict) -> LLMMessage:
    """Deserialize a plain dict back to LLMMessage."""
    tool_call = None
    if d.get("tool_call"):
        tc = d["tool_call"]
        tool_call = ToolCall(
            name=tc.get("name", ""),
            call_id=tc.get("call_id", ""),
            arguments=tc.get("arguments", {}),
        )
    tool_result = None
    if d.get("tool_result"):
        tr = d["tool_result"]
        tool_result = ToolResult(
            name=tr.get("name", ""),
            call_id=tr.get("call_id", ""),
            success=tr.get("success", True),
            result=tr.get("result"),
            error=tr.get("error"),
        )
    return LLMMessage(
        role=d.get("role", "user"),
        content=d.get("content"),
        tool_call=tool_call,
        tool_result=tool_result,
    )


class SessionStore:
    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / "index.jsonl"

    def _session_dir(self, session_id: str) -> Path:
        return self._root / session_id

    def create(self, meta: SessionMeta) -> SessionMeta:
        session_dir = self._session_dir(meta.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        self.save_meta(meta)
        self._append_index(meta)
        return meta

    def get(self, session_id: str) -> SessionMeta | None:
        meta_path = self._session_dir(session_id) / "meta.json"
        if not meta_path.exists():
            return None
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SessionMeta(**data)

    def list(self) -> list[SessionMeta]:
        """Return all sessions sorted by updated_at descending."""
        sessions = []
        if not self._root.exists():
            return sessions
        for entry in self._root.iterdir():
            if entry.is_dir():
                meta_path = entry / "meta.json"
                if meta_path.exists():
                    with open(meta_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    sessions.append(SessionMeta(**data))
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def save_meta(self, meta: SessionMeta) -> None:
        meta.updated_at = __import__("datetime").datetime.now().isoformat()
        session_dir = self._session_dir(meta.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        with open(session_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(meta), f, ensure_ascii=False, indent=2)

    def _append_index(self, meta: SessionMeta) -> None:
        with open(self._index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dataclasses.asdict(meta), ensure_ascii=False) + "\n")

    def append_transcript(self, session_id: str, msg: TranscriptMessage) -> None:
        transcript_path = self._session_dir(session_id) / "transcript.jsonl"
        with open(transcript_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dataclasses.asdict(msg), ensure_ascii=False) + "\n")

    def read_transcript(self, session_id: str) -> list[TranscriptMessage]:
        transcript_path = self._session_dir(session_id) / "transcript.jsonl"
        if not transcript_path.exists():
            return []
        messages = []
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    messages.append(TranscriptMessage(**json.loads(line)))
        return messages

    def make_turn_trajectory_path(self, session_id: str, turn_index: int) -> Path:
        return self._session_dir(session_id) / "turns" / f"{turn_index:04d}_trajectory.json"

    def make_turn_patch_path(self, session_id: str, turn_index: int) -> Path:
        return self._session_dir(session_id) / "turns" / f"{turn_index:04d}_patch.diff"

    def next_turn_index(self, session_id: str) -> int:
        """Compute next turn index as max(existing) + 1, preventing gaps from causing reuse."""
        turns_dir = self._session_dir(session_id) / "turns"
        if not turns_dir.exists():
            return 0
        indices = [
            int(m.group(1))
            for p in turns_dir.glob("*_trajectory.json")
            if (m := re.match(r"^(\d+)_trajectory$", p.stem))
        ]
        return max(indices) + 1 if indices else 0

    def history_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "turns" / "history.jsonl"

    def read_full_history(self, session_id: str) -> list[dict]:
        """Read accumulated LLM message history across all turns."""
        path = self.history_path(session_id)
        if not path.exists():
            return []
        messages = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    messages.append(json.loads(line))
        return messages

    def write_full_history(self, session_id: str, history: list[dict]) -> None:
        """Overwrite history.jsonl with the updated full history after each turn."""
        path = self.history_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for msg in history:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def step_boundaries_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "turns" / "step_boundaries.json"

    def read_step_boundaries(self, session_id: str) -> list[int]:
        """Return persisted step_boundaries, or [] if not yet written."""
        path = self.step_boundaries_path(session_id)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def write_step_boundaries(self, session_id: str, boundaries: list[int]) -> None:
        """Overwrite step_boundaries.json after each turn."""
        path = self.step_boundaries_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(boundaries, f)

    def fork(self, session_id: str) -> SessionMeta:
        import shutil
        from datetime import datetime
        import uuid
        src = self._session_dir(session_id)
        new_id = uuid.uuid4().hex[:12]
        dst = self._session_dir(new_id)
        shutil.copytree(src, dst)
        meta = self.get(new_id)
        if meta is None:
            raise ValueError(f"Session {session_id} not found")
        meta.session_id = new_id
        meta.created_at = datetime.now().isoformat()
        meta.updated_at = datetime.now().isoformat()
        meta.title = f"Fork of {meta.title or session_id}"
        self.save_meta(meta)
        self._append_index(meta)
        return meta
