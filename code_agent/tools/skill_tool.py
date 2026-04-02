# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Skill tool.

Exposes discovered skill packages to the LLM.  The tool resolves a skill name
to a SkillPackage and returns its SKILL.md body plus a resource file index so
the model can read or execute bundled files via the `bash` tool.

Parameter design: single `name` parameter, consistent with the opencode
reference implementation (opencode/packages/opencode/src/tool/skill.ts).

The system prompt renders available skills as "- name: description" lines via
SkillManager.render_prompt_section().  The `name` parameter must be the part
before the colon in that listing.

The description is intentionally static because Tool.description is a
@cached_property and a dynamic description would go stale after the first call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from code_agent.tools.base import Tool, ToolCallArguments, ToolExecResult, ToolParameter

if TYPE_CHECKING:
    from code_agent.skills import SkillManager

logger = logging.getLogger(__name__)


class SkillTool(Tool):
    """Load a skill package and inject its instructions into the LLM context."""

    def __init__(self, model_provider: str | None = None) -> None:
        super().__init__(model_provider)
        self._manager: SkillManager | None = None  # wired by CodeAgent.new_task()

    def get_name(self) -> str:
        return "skill"

    def get_description(self) -> str:
        # Must be static: Tool.description is a @cached_property and would go
        # stale after the first call if it tried to embed the live skill list.
        # The live skill list is rendered into the system prompt instead.
        return (
            "Load a specialized skill that provides domain-specific instructions and "
            "bundled resources. Use this tool when the task matches a skill listed "
            "in the system prompt's <available_skills> section. "
            "After loading, follow the skill's instructions carefully. "
            "Use the `bash` tool to read or execute any bundled file paths listed "
            "in the <skill_files> output block."
        )

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="name",
                type="string",
                description=(
                    "Name of the skill to load, as listed in <available_skills>. "
                    'Each skill appears as "- name: description" — pass the name before the colon.'
                ),
                required=True,
            ),
        ]

    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        if self._manager is None or len(self._manager.all()) == 0:
            # Soft response: skills are disabled or none were discovered.
            # The tool remains visible in the API tools list even when
            # <available_skills> is absent from the system prompt.
            return ToolExecResult(output="No skills are configured for this project.")

        name = str(arguments.get("name") or "").strip()

        result = self._manager.resolve(name)

        if result.status == "not_found":
            available = ", ".join(p.display_name for p in self._manager.all()) or "(none)"
            return ToolExecResult(
                error=f'Skill "{name}" not found. Available: {available}',
                error_code=1,
            )

        if result.status == "ambiguous":
            candidates = "\n".join(
                f"  - {p.display_name} ({p.skill_file})" for p in result.candidates
            )
            return ToolExecResult(
                error=f'Ambiguous skill name "{name}". Candidates:\n{candidates}',
                error_code=1,
            )

        pkg = result.package

        # Resource files: absolute paths so the model can use them directly
        # with the `bash` tool without constructing paths itself.
        # The list is pre-indexed at discovery time and capped at resource_limit.
        files_text = "\n".join(
            f"<file>{pkg.skill_root / r.path}</file>"
            for r in pkg.resources
            if r.kind == "file"
        )

        # Only show a truncation note when the index actually hit the limit,
        # so the model knows there may be additional files in the skill directory.
        resource_limit = self._manager._config.resource_limit
        truncated = len(pkg.resources) >= resource_limit

        # Expose select frontmatter fields that may be useful to the model.
        extra_meta_lines = []
        for key in ("license", "version", "compatibility", "user-invocable"):
            val = pkg.raw_frontmatter.get(key)
            if val:
                extra_meta_lines.append(f"  {key}: {val}")
        extra_meta = ("\n" + "\n".join(extra_meta_lines)) if extra_meta_lines else ""

        lines = [
            f'<skill_content name="{pkg.display_name}">',
            f"# Skill: {pkg.display_name}",
            "",
            pkg.body.strip(),
            "",
            f"Base directory for this skill: {pkg.skill_root}",
            "Relative paths in this skill (e.g., scripts/, references/) are relative to this base directory.",
        ]
        if truncated:
            lines.append(
                "Note: file list is truncated. "
                "Use the `bash` tool to list the skill directory for a complete view."
            )
        lines += [
            "",
            "## Skill metadata",
            f"  description: {pkg.description}{extra_meta}",
            "",
            "<skill_files>",
            files_text,
            "</skill_files>",
            "</skill_content>",
        ]

        return ToolExecResult(output="\n".join(lines))
