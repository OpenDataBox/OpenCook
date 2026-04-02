# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path
import json


KNOWN_SCOPES = {"general", "sqlite", "postgresql", "duckdb", "clickhouse"}

SECTION_ORDER = ["Where to Work", "What Must Be True", "How to Check", "Watch Outs"]

WISDOM_TEMPLATE = """\
# {scope} Patterns

## Where to Work
<!-- Files, modules, entry points, and registration sites typically visited for this class of task -->

## What Must Be True
<!-- Behavioral rules, boundary conditions, and compatibility requirements that must hold -->

## How to Check
<!-- Most effective verification paths, key tests, common commands, and failure triage order -->

## Watch Outs
<!-- Common pitfalls, misleading signals, edge cases, and steps known to waste time -->
"""


class ProjectMemory:
    """
    Layer C: Manages per-scope wisdom Markdown files.
    Scope is an abstract dimension; database name is the common concrete scope.
    The main execution path only reads official wisdom; candidates are written at task end.
    Official wisdom is updated only via the offline consolidation methods.
    """

    def __init__(self, memory_root: Path):
        self._wisdom_dir = memory_root / "wisdom"
        self._wisdom_dir.mkdir(parents=True, exist_ok=True)

    def _scope_path(self, scope: str) -> Path:
        return self._wisdom_dir / f"{scope}.md"

    def _candidate_path(self, scope: str) -> Path:
        return self._wisdom_dir / f"{scope}_candidates.jsonl"

    def ensure_scope_file(self, scope: str) -> Path:
        """Create a wisdom file from the template if it does not already exist."""
        path = self._scope_path(scope)
        if not path.exists():
            path.write_text(
                WISDOM_TEMPLATE.format(scope=scope.capitalize()),
                encoding="utf-8"
            )
        return path

    def read_wisdom_merged(self, scopes: list[str]) -> str | None:
        """
        Read wisdom files for the given scopes in order and merge them section by section.
        'general' is loaded first; database-specific scopes follow (same-section content appended).
        Duplicate bullets are deduplicated across scopes.
        Returns the merged Markdown string, or None when all files are empty / missing.
        """
        merged: dict[str, list[str]] = {s: [] for s in SECTION_ORDER}
        seen: dict[str, set[str]] = {s: set() for s in SECTION_ORDER}
        any_content = False

        for scope in scopes:
            path = self._scope_path(scope)
            if not path.exists():
                continue
            current_section = None
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    heading = line[3:].strip()
                    current_section = heading if heading in merged else None
                elif current_section and line.strip() and not line.strip().startswith("<!--"):
                    bullet = line.rstrip()
                    if bullet not in seen[current_section]:
                        seen[current_section].add(bullet)
                        merged[current_section].append(bullet)
                        any_content = True

        if not any_content:
            return None

        lines = []
        for section in SECTION_ORDER:
            lines.append(f"## {section}")
            lines.extend(merged[section])
            lines.append("")
        return "\n".join(lines).strip()

    def write_candidate(self, scope: str, data: dict) -> None:
        """
        Append a candidate hint record to {scope}_candidates.jsonl.
        Called after execute_task(); does not touch the official wisdom/*.md files.
        Promotion from candidates to official wisdom is a separate offline consolidation step.
        """
        path = self._candidate_path(scope)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # Offline consolidation — not called from the main execution path
    # ------------------------------------------------------------------

    def consolidate(
        self,
        scope: str,
        min_frequency: int = 10,           # Where/How/Must: min task-level occurrences to promote
        watch_out_min_frequency: int = 5,   # Watch Outs: lower bar since errors are less frequent
        max_per_section: int = 8,           # hard cap on bullets per section after append
        recency_days: int = 90,             # ignore candidates older than this many days
    ) -> bool:
        """
        Promote high-frequency hints from {scope}_candidates.jsonl into official {scope}.md.
        Only appends new bullets; never removes existing content.
        Returns True if at least one bullet was written.

        Merge gates (applied to every candidate bullet):
          Gate 1  — Section validity: section must be in SECTION_ORDER.
          Gate 2  — Bullet format: must start with "- ".
          Gate 3  — Success constraint (section-dependent):
                      Watch Outs: unconstrained (errors can come from successful tasks too).
                      Other sections: at least one record with success=True required.
          Gate 4  — Per-section frequency threshold:
                      Where to Work / How to Check  >= min_frequency (default 2).
                      Watch Outs                    >= watch_out_min_frequency (default 1).
                      What Must Be True             >= min_frequency * 2 (LLM candidates only;
                                                       heuristic extractor does not emit this section).
          Gate 5  — Deduplication: exact match against existing official wisdom bullets.
          Gate 6  — Capacity cap: section bullet count after append <= max_per_section.
        """
        from datetime import datetime, timezone, timedelta
        from collections import defaultdict

        candidate_path = self._candidate_path(scope)
        if not candidate_path.exists():
            return False

        cutoff = datetime.now(timezone.utc) - timedelta(days=recency_days)

        candidates: list[dict] = []
        for lineno, line in enumerate(candidate_path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception as e:
                print(f"[ProjectMemory.consolidate] JSON parse error at line {lineno} "
                      f"of {candidate_path}: {e}")
                continue
            try:
                ts = datetime.fromisoformat(rec.get("start_time", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except Exception:
                pass  # unparseable timestamp — do not filter out
            candidates.append(rec)

        if not candidates:
            return False

        _SECTION_MIN_FREQ = {
            "Where to Work": min_frequency,
            "How to Check": min_frequency,
            "Watch Outs": watch_out_min_frequency,
            "What Must Be True": min_frequency * 2,
        }

        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for rec in candidates:
            section = rec.get("section", "")
            bullet = rec.get("bullet", "")
            # Gate 1 + Gate 2
            if section not in SECTION_ORDER or not bullet.startswith("- "):
                continue
            groups[(section, bullet)].append(rec)

        # Read existing official wisdom
        wisdom_path = self._scope_path(scope)
        self.ensure_scope_file(scope)
        existing_text = wisdom_path.read_text(encoding="utf-8")

        existing_bullets: dict[str, set[str]] = {s: set() for s in SECTION_ORDER}
        current_section = None
        for line in existing_text.splitlines():
            if line.startswith("## "):
                heading = line[3:].strip()
                current_section = heading if heading in existing_bullets else None
            elif current_section and line.strip() and not line.strip().startswith("<!--"):
                existing_bullets[current_section].add(line.rstrip())

        by_section: dict[str, list[tuple[str, int]]] = {s: [] for s in SECTION_ORDER}
        for (section, bullet), recs in groups.items():
            freq = len(recs)
            threshold = _SECTION_MIN_FREQ[section]
            # Gate 3
            if section != "Watch Outs" and not any(r.get("success", False) for r in recs):
                continue
            # Gate 4
            if freq < threshold:
                continue
            # Gate 5
            if bullet in existing_bullets[section]:
                continue
            by_section[section].append((bullet, freq))

        section_additions: dict[str, list[str]] = {}
        for section in SECTION_ORDER:
            existing = existing_bullets[section]
            capacity = max(0, max_per_section - len(existing))  # Gate 6
            if capacity == 0 or not by_section[section]:
                continue
            sorted_bullets = sorted(by_section[section], key=lambda x: x[1], reverse=True)
            new_bullets = [b for b, _ in sorted_bullets[:capacity]]
            if new_bullets:
                section_additions[section] = new_bullets

        if not section_additions:
            return False

        lines = existing_text.rstrip().splitlines()
        result_lines: list[str] = []
        current_section = None
        i = 0
        while i < len(lines):
            line = lines[i]
            result_lines.append(line)
            if line.startswith("## "):
                current_section = line[3:].strip()
            is_last_line = (i == len(lines) - 1)
            next_is_section = (i + 1 < len(lines) and lines[i + 1].startswith("## "))
            if current_section and (is_last_line or next_is_section):
                result_lines.extend(section_additions.get(current_section, []))
            i += 1

        wisdom_path.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
        return True

    def consolidate_general(
        self,
        db_scopes: list[str] | None = None,
        min_scopes: int = 3,               # Where/How/Must: min distinct DBs required to promote
        watch_out_min_scopes: int = 2,      # Watch Outs: min distinct DBs (lower bar)
        min_scope_frequency: int = 5,       # per-DB prerequisite: bullet must appear >= N times before that DB counts
        max_per_section: int = 8,           # hard cap on bullets per section after append
        recency_days: int = 90,             # ignore candidates older than this many days
    ) -> bool:
        """
        Aggregate DB-specific candidates across multiple scopes to populate general.md.

        A bullet is promoted to general.md when it appears in >= min_scopes distinct scopes,
        each contributing >= min_scope_frequency occurrences of that bullet.
        Watch Outs use a lower threshold (watch_out_min_scopes=1) because defensive
        experience transfers across databases even if seen in only one DB.

        Gate logic is identical to consolidate(), with Gate 4 redefined as:
          "number of scopes in which this bullet meets min_scope_frequency" >= threshold.
        """
        from datetime import datetime, timezone, timedelta
        from collections import defaultdict

        if db_scopes is None:
            db_scopes = [s for s in KNOWN_SCOPES if s != "general"]

        cutoff = datetime.now(timezone.utc) - timedelta(days=recency_days)

        bullet_scopes: dict[tuple[str, str], set[str]] = defaultdict(set)

        for scope in db_scopes:
            path = self._candidate_path(scope)
            if not path.exists():
                continue
            scope_counts: dict[tuple[str, str], int] = defaultdict(int)
            scope_has_success: dict[tuple[str, str], bool] = defaultdict(bool)
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception as e:
                    print(f"[ProjectMemory.consolidate_general] JSON parse error at line {lineno} "
                          f"of {path}: {e}")
                    continue
                try:
                    ts = datetime.fromisoformat(rec.get("start_time", ""))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < cutoff:
                        continue
                except Exception:
                    pass
                section = rec.get("section", "")
                bullet = rec.get("bullet", "")
                if section not in SECTION_ORDER or not bullet.startswith("- "):
                    continue
                scope_counts[(section, bullet)] += 1
                if rec.get("success", False):
                    scope_has_success[(section, bullet)] = True

            for key, cnt in scope_counts.items():
                if cnt >= min_scope_frequency:
                    # Gate 3 (same as consolidate): non-Watch-Outs require at least
                    # one success=True record within this scope to avoid promoting
                    # failed attempts that happen to appear across many databases.
                    if key[0] != "Watch Outs" and not scope_has_success[key]:
                        continue
                    bullet_scopes[key].add(scope)

        if not bullet_scopes:
            return False

        self.ensure_scope_file("general")
        wisdom_path = self._scope_path("general")
        existing_text = wisdom_path.read_text(encoding="utf-8")

        existing_bullets: dict[str, set[str]] = {s: set() for s in SECTION_ORDER}
        current_section = None
        for line in existing_text.splitlines():
            if line.startswith("## "):
                heading = line[3:].strip()
                current_section = heading if heading in existing_bullets else None
            elif current_section and line.strip() and not line.strip().startswith("<!--"):
                existing_bullets[current_section].add(line.rstrip())

        by_section: dict[str, list[tuple[str, int]]] = {s: [] for s in SECTION_ORDER}
        for (section, bullet), scopes_set in bullet_scopes.items():
            if section == "Watch Outs":
                threshold = watch_out_min_scopes
            elif section == "What Must Be True":
                # Mirror consolidate()'s min_frequency * 2 ratio: higher bar for
                # invariant claims that are harder to verify and riskier if wrong.
                threshold = min_scopes * 2
            else:
                threshold = min_scopes
            if len(scopes_set) < threshold:
                continue
            if bullet in existing_bullets[section]:
                continue
            by_section[section].append((bullet, len(scopes_set)))

        section_additions: dict[str, list[str]] = {}
        for section in SECTION_ORDER:
            capacity = max(0, max_per_section - len(existing_bullets[section]))
            if capacity == 0 or not by_section[section]:
                continue
            sorted_bullets = sorted(by_section[section], key=lambda x: x[1], reverse=True)
            new_bullets = [b for b, _ in sorted_bullets[:capacity]]
            if new_bullets:
                section_additions[section] = new_bullets

        if not section_additions:
            return False

        lines = existing_text.rstrip().splitlines()
        result_lines: list[str] = []
        current_section = None
        i = 0
        while i < len(lines):
            line = lines[i]
            result_lines.append(line)
            if line.startswith("## "):
                current_section = line[3:].strip()
            is_last_line = (i == len(lines) - 1)
            next_is_section = (i + 1 < len(lines) and lines[i + 1].startswith("## "))
            if current_section and (is_last_line or next_is_section):
                result_lines.extend(section_additions.get(current_section, []))
            i += 1

        wisdom_path.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
        return True
