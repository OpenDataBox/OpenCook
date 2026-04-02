# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Skill discovery helpers.

Provides walk_up_to_root() for ancestor-directory scanning and
scan_standalone_root() for finding all SKILL.md packages inside a root
directory.  SkillManager.discover() calls these to visit all configured
roots in tier order.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from code_agent.skills.loader import load_standalone_package
from code_agent.skills.model import SkillPackage, SkillSourceScope

logger = logging.getLogger(__name__)

# Built-in skills directory: open-cookbook/ at the repository root.
# This file lives at code_agent/skills/discovery.py, so:
#   .parent           → code_agent/skills/
#   .parent.parent    → code_agent/
#   .parent.parent.parent → <repo-root>/
BUILTIN_ROOT: Path = Path(__file__).parent.parent.parent / "open-cookbook"


def walk_up_to_root(start: Path) -> Iterator[Path]:
    """Yield ancestor directories from *start* upward.

    Stops at the nearest git root (directory containing .git) or the
    filesystem root, whichever comes first.  The start directory itself
    is yielded first.
    """
    current = start.resolve()
    while True:
        yield current
        if (current / ".git").exists():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent


def scan_standalone_root(
    root: Path,
    scope: SkillSourceScope,
    resource_limit: int,
) -> list[SkillPackage]:
    """Scan *root*/*/SKILL.md and return all successfully loaded packages.

    Candidate subdirectories are sorted by their resolved absolute path
    before loading, ensuring a stable, platform-independent order.
    discovery_order is NOT assigned here; SkillManager sets it after each call.
    """
    if not root.is_dir():
        return []

    try:
        candidates = sorted(root.iterdir(), key=lambda p: str(p.resolve()))
    except OSError as exc:
        logger.warning("Cannot scan skill root %s: %s", root, exc)
        return []

    packages: list[SkillPackage] = []
    for child in candidates:
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            continue
        pkg = load_standalone_package(skill_file, scope, resource_limit)
        if pkg is not None:
            packages.append(pkg)

    return packages
