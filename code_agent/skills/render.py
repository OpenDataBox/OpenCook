# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""System-prompt renderer for the skill system.

Generates the <available_skills> block that is appended to the CodeAgent
system prompt so the LLM knows which skills are available and how to invoke them.
Each skill is rendered as a single line (name + description) to minimise token
cost.  Paths and resources are returned by the skill tool after loading, so they
are not included here.
"""

from __future__ import annotations

from code_agent.skills.model import SkillPackage


def render_skills_section(packages: list[SkillPackage]) -> str:
    """Return the <available_skills> prompt block, or "" if no skills are available."""
    if not packages:
        return ""

    lines = [
        "<available_skills>",
        "A skill provides domain-specific instructions stored in a SKILL.md file.",
        "Call the `skill` tool with the skill name to load full instructions.",
        "",
        "### Available skills",
    ]
    for p in packages:
        lines.append(f"- {p.display_name}: {p.description}")

    lines += [
        "",
        "### How to use skills",
        "- When a task matches a skill, call the `skill` tool first.",
        "- After loading, follow the skill's instructions carefully.",
        "- Use the `bash` tool to read or run any bundled file paths listed in the output.",
        "- If multiple skills apply, load the most specific one first.",
        "</available_skills>",
    ]
    return "\n".join(lines)
