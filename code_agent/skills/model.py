# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Data model for the skill system.

Defines the core data structures used across all skill modules:
SkillPackage (one discovered skill), SkillLookupResult (resolve() output),
SkillResourceEntry (a file or directory under a skill root), and
SkillSourceScope (where a skill was found).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SkillSourceScope(str, Enum):
    """Indicates where a skill was discovered."""
    BUILTIN = "builtin"   # Shipped with OpenCook (open-cookbook/)
    USER    = "user"      # OS-user global directory (~/.opencook/skills/)
    PROJECT = "project"   # Current project-local directory (.opencook/skills/)


@dataclass
class SkillResourceEntry:
    """A single file or directory entry under a skill root."""
    path: str   # Path relative to skill_root
    kind: str   # "file" or "dir"


@dataclass
class SkillLookupResult:
    """Result returned by SkillManager.resolve()."""
    status: str                            # "found" | "not_found" | "ambiguous"
    package: SkillPackage | None = None    # Set when status == "found"
    candidates: list[SkillPackage] = field(default_factory=list)  # Set when ambiguous


@dataclass
class SkillPackage:
    """A fully loaded, indexed skill package (standalone only in Phase 1)."""

    # ── Identity ──────────────────────────────────────────────────────────────
    skill_id: str
    # Format: "{scope}::{canonical_skill_file_path}"
    # Unique per (scope × canonical path).  Path-level deduplication is handled
    # separately via SkillManager._path_index keyed on skill_file (already resolved).

    # ── User-facing identity ──────────────────────────────────────────────────
    display_name: str   # Frontmatter "name" field, or folder name as fallback
    description:  str   # Frontmatter "description" field, or ""
    aliases: list[str]  # [display_name, folder_name, slug] — deduplicated

    # ── Classification ────────────────────────────────────────────────────────
    source_scope: SkillSourceScope

    # ── Paths (all canonical/resolved) ───────────────────────────────────────
    skill_root: Path   # Directory containing SKILL.md
    skill_file: Path   # Absolute canonical path to SKILL.md

    # ── Content ───────────────────────────────────────────────────────────────
    body:            str                    # SKILL.md body text (after closing ---)
    raw_frontmatter: dict[str, Any]         # All parsed frontmatter keys
    sidecar_openai:  dict[str, Any] | None  # Parsed agents/openai.yaml, or None

    # ── Runtime environment ───────────────────────────────────────────────────
    env: dict[str, str]
    # Key: OPENCOOK_SKILL_DIR → absolute path to skill_root.
    # Injected into tool output so the LLM can reference bundled scripts as
    # $OPENCOOK_SKILL_DIR/scripts/foo.py in bash commands.

    # ── Sorting & resources ───────────────────────────────────────────────────
    discovery_order: int = 0  # Higher = higher priority; assigned by SkillManager
    resources: list[SkillResourceEntry] = field(default_factory=list)
