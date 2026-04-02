# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Skill package loader.

Reads a single SKILL.md from disk and constructs a SkillPackage dataclass.
Parsing follows a fail-open policy: malformed or missing frontmatter falls back
to the folder name rather than aborting discovery.  The only hard failure is an
unreadable file (OSError / PermissionError).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from code_agent.skills.model import SkillPackage, SkillResourceEntry, SkillSourceScope

logger = logging.getLogger(__name__)

# Matches the closing "---" delimiter of YAML front matter (allows trailing spaces).
_FM_CLOSE = re.compile(r'\n---[ \t]*(\r?\n|$)')


def build_env(skill_root: Path) -> dict[str, str]:
    """Return the runtime environment variable map for a skill package."""
    return {"OPENCOOK_SKILL_DIR": str(skill_root)}


def _parse_skill_md(path: Path) -> tuple[dict[str, Any], str]:
    """Parse a SKILL.md file and return (frontmatter_dict, body_text).

    Raises OSError when the file cannot be read.
    On any structural or YAML problem, logs a warning and returns ({}, full_text).
    """
    text = path.read_text(encoding="utf-8")

    if not text.startswith("---"):
        logger.warning("No frontmatter in %s; falling back to folder name", path)
        return {}, text

    m = _FM_CLOSE.search(text)
    if m is None:
        logger.warning("Unclosed frontmatter in %s; falling back to folder name", path)
        return {}, text

    fm_text = text[3 : m.start()].strip()
    body    = text[m.end() :].strip()

    try:
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            raise TypeError("frontmatter is not a mapping")
    except Exception as exc:
        logger.warning(
            "Malformed frontmatter in %s (%s); falling back to folder name", path, exc
        )
        return {}, text

    return fm, body


def _index_resources(skill_root: Path, limit: int) -> list[SkillResourceEntry]:
    """Walk skill_root and return up to *limit* resource entries, excluding SKILL.md."""
    entries: list[SkillResourceEntry] = []
    try:
        for item in sorted(skill_root.rglob("*"), key=lambda p: str(p)):
            if item.name == "SKILL.md":
                continue
            rel  = item.relative_to(skill_root)
            kind = "dir" if item.is_dir() else "file"
            entries.append(SkillResourceEntry(path=str(rel), kind=kind))
            if len(entries) >= limit:
                break
    except OSError as exc:
        logger.warning("Cannot index resources under %s: %s", skill_root, exc)
    return entries


def _make_aliases(display_name: str, folder_name: str) -> list[str]:
    """Build a deduplicated alias list: [display_name, folder_name, slug]."""
    slug = display_name.lower().replace(" ", "-").replace("_", "-")
    seen: list[str] = []
    for s in (display_name, folder_name, slug):
        if s and s not in seen:
            seen.append(s)
    return seen


def load_standalone_package(
    skill_file: Path,
    source_scope: SkillSourceScope,
    resource_limit: int,
) -> SkillPackage | None:
    """Load one standalone skill package from *skill_file*.

    Returns None only when the file is unreadable (OSError / PermissionError).
    All other problems are handled gracefully via warnings and fallbacks.
    """
    skill_root  = skill_file.parent
    folder_name = skill_root.name

    try:
        fm, body = _parse_skill_md(skill_file)
    except OSError as exc:
        logger.error("Cannot read skill file %s: %s", skill_file, exc)
        return None

    display_name = str(fm.get("name") or folder_name)
    description  = str(fm.get("description") or "")

    # Optional Codex-style sidecar (parsed and stored; no runtime enforcement).
    sidecar: dict[str, Any] | None = None
    sidecar_path = skill_root / "agents" / "openai.yaml"
    if sidecar_path.exists():
        try:
            sidecar = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Cannot parse sidecar %s: %s", sidecar_path, exc)

    resolved_file = skill_file.resolve()
    resolved_root = skill_root.resolve()

    return SkillPackage(
        skill_id        = f"{source_scope.value}::{resolved_file}",
        display_name    = display_name,
        description     = description,
        aliases         = _make_aliases(display_name, folder_name),
        source_scope    = source_scope,
        skill_root      = resolved_root,
        skill_file      = resolved_file,
        body            = body,
        raw_frontmatter = fm,
        sidecar_openai  = sidecar,
        env             = build_env(resolved_root),
        resources       = _index_resources(resolved_root, resource_limit),
    )
