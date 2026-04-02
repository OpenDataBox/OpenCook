# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

from dataclasses import dataclass


@dataclass
class MemoryConfig:
    enabled: bool = True

    # Global memory root directory (~ is expanded); independent of project_path
    memory_root: str = "~/.opencook/memory"

    # Layer A: Working Memory + Offload Compaction
    compaction_enabled: bool = True
    # Trigger bulk compaction when estimated history tokens exceed context_window * ratio
    compaction_token_ratio: float = 0.75
    # Eagerly archive a single tool_result whose token estimate exceeds this threshold
    compaction_spike_tokens: int = 8000
    # Number of most-recent steps shielded from bulk compaction
    compaction_protect_recent_steps: int = 5

    # Layer B: Episodic Memory
    session_write_enabled: bool = True
    # Number of relevant past episodes injected into the initial user prompt
    episode_inject_top_k: int = 3

    # Layer C: Project Memory
    # Write candidate hints at task end; does not touch official wisdom files
    candidate_write_enabled: bool = True
