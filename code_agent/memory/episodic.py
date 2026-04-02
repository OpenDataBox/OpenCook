# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import dataclasses
from pathlib import Path

from code_agent.memory.schema import EpisodeRecord

# Field names present in EpisodeRecord — used to filter unknown keys from old
# JSONL records so backward-incompatible fields don't cause TypeError on load.
_EPISODE_FIELDS: frozenset[str] = frozenset(
    f.name for f in dataclasses.fields(EpisodeRecord)
)

# Defaults for fields added after the initial schema release.
# Populated via dict.setdefault() before constructing EpisodeRecord from disk.
_EPISODE_DEFAULTS: dict[str, object] = {
    "category": "",
    "tool_usage": {},
}


class EpisodicMemory:
    """
    Layer B: Manages the per-project JSONL episodic index.
    Writes structured EpisodeRecord entries derived from trajectory_data,
    and supports querying by category / database for prompt injection.
    """

    def __init__(self, memory_root: Path):
        self._index_path = memory_root / "episodes_index.jsonl"
        self._index_path.parent.mkdir(parents=True, exist_ok=True)

    def write_episode(self, trajectory_data: dict, trajectory_path: str) -> None:
        """
        Extract fields from trajectory_data and append an EpisodeRecord to the JSONL index.
        All aggregate counts (tool_usage, total_steps, error_steps) span the full trajectory
        across all agent types.
        """
        task = trajectory_data.get("task", {})
        steps = trajectory_data.get("agent_steps", [])
        interactions = trajectory_data.get("llm_interactions", [])

        # aggregate across the entire trajectory (all agent types)
        error_steps = len([s for s in steps if s.get("error")])
        tool_usage: dict[str, int] = {}
        for s in steps:
            for tc in (s.get("tool_calls") or []):
                name = tc.get("name", "unknown")
                tool_usage[name] = tool_usage.get(name, 0) + 1

        total_in = sum(
            (i.get("response", {}).get("usage", {}).get("input_tokens") or 0)
            for i in interactions
        )
        total_out = sum(
            (i.get("response", {}).get("usage", {}).get("output_tokens") or 0)
            for i in interactions
        )

        record = EpisodeRecord(
            func_name=task.get("func_name", ""),
            database=task.get("database", ""),
            directory=task.get("directory", ""),
            category=task.get("category", ""),
            success=trajectory_data.get("success", False),
            final_result=trajectory_data.get("final_result"),
            execution_time=trajectory_data.get("execution_time", 0.0),
            start_time=trajectory_data.get("start_time", ""),
            total_steps=len(steps),
            error_steps=error_steps,
            tool_usage=tool_usage,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            trajectory_path=trajectory_path,
        )

        with open(self._index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dataclasses.asdict(record), ensure_ascii=False) + "\n")

    def query(self, category: str, database: str, top_k: int = 3) -> list[EpisodeRecord]:
        """Return the most-recent K successful episodes matching category + database.

        Filters:
          - success=True only
          - category + database when category is non-empty
          - database only when category is empty (fallback)
        Returns [] if no matches found in either case.
        """
        if not self._index_path.exists():
            return []
        records: list[EpisodeRecord] = []
        with open(self._index_path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if not d.get("success", False):
                        continue
                    if d.get("database") != database:
                        continue
                    if category and d.get("category") != category:
                        continue
                    known = {k: v for k, v in d.items() if k in _EPISODE_FIELDS}
                    for field, default in _EPISODE_DEFAULTS.items():
                        known.setdefault(field, default)
                    records.append(EpisodeRecord(**known))
                except Exception as e:
                    print(f"[EpisodicMemory] Failed to parse episode at line {lineno} "
                          f"in {self._index_path}: {e}")
        records.sort(key=lambda r: r.start_time, reverse=True)
        return records[:top_k]

    def query_by_database(self, database: str, top_k: int = 5) -> list[EpisodeRecord]:
        """Return the most-recent K episodes for a given database (used in wisdom analysis)."""
        if not self._index_path.exists():
            return []
        records: list[EpisodeRecord] = []
        with open(self._index_path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d.get("database") == database:
                        known = {k: v for k, v in d.items() if k in _EPISODE_FIELDS}
                        for field, default in _EPISODE_DEFAULTS.items():
                            known.setdefault(field, default)
                        records.append(EpisodeRecord(**known))
                except Exception as e:
                    print(f"[EpisodicMemory] Failed to parse episode at line {lineno} "
                          f"in {self._index_path}: {e}")
        records.sort(key=lambda r: r.start_time, reverse=True)
        return records[:top_k]
