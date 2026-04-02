# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Skill manager.

SkillManager orchestrates multi-root discovery, maintains lookup indexes, and
exposes a simple query API (resolve / all / render_prompt_section).

Discovery visits roots in tier order from lowest to highest priority:
  1. Built-in   (open-cookbook/)
  2. User-global (~/.opencook/skills/)
  3. Project-local walk-up (.opencook/skills/, farthest ancestor first → cwd last)
  4. Extra standalone paths (explicit root dirs from config)
  5. Extra standalone packages (explicit single-skill dirs from config)

Each package receives a monotonically increasing discovery_order; higher means
higher priority.  Name collisions across different paths are kept — ambiguity is
surfaced at resolve() time, never silently resolved.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from code_agent.skills.model import SkillLookupResult, SkillPackage, SkillSourceScope

if TYPE_CHECKING:
    from code_agent.utils.config import SkillsConfig

logger = logging.getLogger(__name__)


class SkillManager:
    """Discovers, indexes, and serves skill packages for a single project root."""

    def __init__(self, cwd: str, config: "SkillsConfig") -> None:
        self._cwd    = Path(cwd)
        self._config = config
        self._counter: int = 0

        # Primary store: skill_id → SkillPackage
        self._packages_by_id: dict[str, SkillPackage] = {}

        # Lookup indexes (value = list of skill_ids sharing that key)
        self._name_index:   dict[str, list[str]] = {}   # display_name → [skill_ids]
        self._folder_index: dict[str, list[str]] = {}   # folder name  → [skill_ids]
        self._alias_index:  dict[str, list[str]] = {}   # any alias    → [skill_ids]

        # Deduplication index: canonical path string → skill_id
        # Key is str(skill_file) which is already resolved in the loader.
        self._path_index: dict[str, str] = {}

    # ── Discovery ─────────────────────────────────────────────────────────────

    def discover(self) -> None:
        """Populate all indexes by scanning every configured root in tier order."""
        from code_agent.skills.discovery import (
            BUILTIN_ROOT,
            scan_standalone_root,
            walk_up_to_root,
        )
        from code_agent.skills.loader import load_standalone_package

        def _scan(root: Path, scope: SkillSourceScope) -> None:
            for pkg in scan_standalone_root(root, scope, self._config.resource_limit):
                self._counter += 1
                pkg.discovery_order = self._counter
                self._add_package(pkg)

        # Tier 1 — built-in (lowest priority)
        _scan(BUILTIN_ROOT, SkillSourceScope.BUILTIN)

        # Tier 2 — user-global standalone roots
        for root_str in self._config.standalone_user_roots:
            _scan(Path(root_str), SkillSourceScope.USER)

        # Tiers 3+4 — project-local walk-up (farthest ancestor first, cwd last)
        for suffix in self._config.standalone_project_roots:
            ancestors = list(walk_up_to_root(self._cwd))
            ancestors.reverse()  # farthest → nearest → cwd
            for ancestor in ancestors:
                _scan(ancestor / suffix, SkillSourceScope.PROJECT)

        # Tier 5 — extra root dirs (each child is a standalone skill package)
        for root_str in self._config.extra_standalone_paths:
            _scan(Path(root_str), SkillSourceScope.PROJECT)

        # Tier 6 — extra direct skill packages (each path IS the skill root)
        for pkg_str in self._config.extra_standalone_packages:
            pkg_path   = Path(pkg_str)
            skill_file = pkg_path / "SKILL.md"
            if not skill_file.is_file():
                logger.warning(
                    "extra_standalone_packages entry %s has no SKILL.md; skipping",
                    pkg_path,
                )
                continue
            pkg = load_standalone_package(
                skill_file, SkillSourceScope.PROJECT, self._config.resource_limit
            )
            if pkg is not None:
                self._counter += 1
                pkg.discovery_order = self._counter
                self._add_package(pkg)

    # ── Index management ──────────────────────────────────────────────────────

    def _add_package(self, pkg: SkillPackage) -> None:
        """Insert *pkg* into all indexes, handling canonical-path deduplication.

        When the same canonical path has already been indexed (e.g. via a
        symlink or a repeated root), keep whichever entry has the higher
        discovery_order (= higher priority) and discard the other.
        """
        canonical = str(pkg.skill_file)  # already resolved by the loader

        if canonical in self._path_index:
            existing_id  = self._path_index[canonical]
            existing_pkg = self._packages_by_id.get(existing_id)
            if existing_pkg and pkg.discovery_order <= existing_pkg.discovery_order:
                return  # existing entry has higher or equal priority; skip
            # New entry has higher priority — evict the old one.
            self._remove_from_indexes(existing_id)

        self._path_index[canonical]              = pkg.skill_id
        self._packages_by_id[pkg.skill_id]       = pkg

        self._name_index.setdefault(pkg.display_name, [])
        if pkg.skill_id not in self._name_index[pkg.display_name]:
            self._name_index[pkg.display_name].append(pkg.skill_id)

        folder = pkg.skill_root.name
        self._folder_index.setdefault(folder, [])
        if pkg.skill_id not in self._folder_index[folder]:
            self._folder_index[folder].append(pkg.skill_id)

        for alias in pkg.aliases:
            self._alias_index.setdefault(alias, [])
            if pkg.skill_id not in self._alias_index[alias]:
                self._alias_index[alias].append(pkg.skill_id)

    def _remove_from_indexes(self, skill_id: str) -> None:
        """Remove *skill_id* from all indexes except _path_index.

        _path_index is updated by the caller immediately after this call.
        """
        pkg = self._packages_by_id.pop(skill_id, None)
        if pkg is None:
            return

        def _drop(idx: dict[str, list[str]], key: str) -> None:
            if key in idx:
                idx[key] = [i for i in idx[key] if i != skill_id]
                if not idx[key]:
                    del idx[key]

        _drop(self._name_index,   pkg.display_name)
        _drop(self._folder_index, pkg.skill_root.name)
        for alias in pkg.aliases:
            _drop(self._alias_index, alias)

    # ── Public API ────────────────────────────────────────────────────────────

    def all(self) -> list[SkillPackage]:
        """Return all packages sorted by discovery_order descending (highest priority first)."""
        return sorted(
            self._packages_by_id.values(),
            key=lambda p: p.discovery_order,
            reverse=True,
        )

    def resolve(self, query: str) -> SkillLookupResult:
        """Look up a skill by path, skill_id, display name, alias, or folder name.

        Match priority:
          1. Canonical path  → always unique → "found"
          2. Exact skill_id  → always unique → "found"
          3. display_name    → "found" if unique, else "ambiguous"
          4. alias           → "found" if unique, else "ambiguous"
          5. folder name     → "found" if unique, else "ambiguous"

        Ambiguous candidates are sorted by discovery_order descending.
        """
        if not query:
            return SkillLookupResult(status="not_found")

        # 1. Path lookup
        try:
            canonical = str(Path(query).resolve())
            if canonical in self._path_index:
                sid = self._path_index[canonical]
                return SkillLookupResult(status="found", package=self._packages_by_id[sid])
        except (OSError, ValueError):
            pass

        # 2. Exact skill_id
        if query in self._packages_by_id:
            return SkillLookupResult(status="found", package=self._packages_by_id[query])

        # 3–5. name → alias → folder
        for idx in (self._name_index, self._alias_index, self._folder_index):
            ids = idx.get(query)
            if not ids:
                continue
            if len(ids) == 1:
                return SkillLookupResult(
                    status="found", package=self._packages_by_id[ids[0]]
                )
            candidates = sorted(
                (self._packages_by_id[i] for i in ids),
                key=lambda p: p.discovery_order,
                reverse=True,
            )
            return SkillLookupResult(status="ambiguous", candidates=candidates)

        return SkillLookupResult(status="not_found")

    def render_prompt_section(self) -> str:
        """Render the <available_skills> system-prompt block."""
        from code_agent.skills.render import render_skills_section
        return render_skills_section(self.all())
