# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EpisodeRecord:
    """
    Layer B: Per-task history index entry.
    Fields are derived deterministically from trajectory_data (aggregate stats)
    plus index metadata (trajectory_path, etc.).
    No fields requiring separate business-logic maintenance are introduced.
    """
    # from trajectory_data["task"]
    func_name: str
    database: str
    directory: str
    category: str              # function category (e.g. "math", "string", "aggregate")

    # from trajectory_data top level
    success: bool
    final_result: str | None
    execution_time: float
    start_time: str

    # aggregated from ALL agent_steps (entire trajectory, all agent types)
    total_steps: int           # total steps across the entire trajectory
    error_steps: int           # steps with a non-empty error field
    tool_usage: dict[str, int] # tool_name -> call count across the entire trajectory

    # aggregated from llm_interactions
    total_input_tokens: int
    total_output_tokens: int

    # metadata
    trajectory_path: str       # path to the full trajectory JSON file


@dataclass
class CompactStub:
    """Placeholder that replaces an offloaded tool_result in an LLMMessage."""
    archive_path: str
    tool_name: str
    original_tokens: int
    step_number: int
    call_id: str

    def to_text(self) -> str:
        # Keep stubs minimal — the snapshot injected during bulk compaction provides
        # the shared usage guide (grep instructions) and lists all archived files.
        return (
            f"[COMPACTED at step {self.step_number}] "
            f"{self.tool_name} | ~{self.original_tokens} tokens | {self.archive_path}"
        )
