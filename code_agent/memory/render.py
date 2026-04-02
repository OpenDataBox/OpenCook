# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Prompt rendering for the memory system.

Converts Layer B / C memory into prompt injection fragments that are appended
to the system prompt (project wisdom) and the initial user prompt (past attempts).
All functions are pure — no I/O, no side effects.
"""

from __future__ import annotations

from code_agent.memory.schema import EpisodeRecord

# Maximum characters taken from final_result for the result snippet in past_attempts
_RESULT_SNIPPET_MAX_CHARS = 200

# Maximum number of top tools shown per episode in past_attempts
_TOP_TOOLS_COUNT = 3


def build_system_addon(scopes: list[str], wisdom: str | None) -> str:
    """
    Build the project wisdom block appended to the end of the system prompt.
    Returns an empty string when wisdom is None or empty.
    """
    if not wisdom:
        return ""
    scopes_str = ",".join(scopes)
    return (
        f"\n\n<project_wisdom scopes=\"{scopes_str}\">\n"
        f"{wisdom}\n"
        f"</project_wisdom>"
    )


def build_user_addon(episodes: list[EpisodeRecord]) -> str:
    """
    Build the past_attempts block appended to the end of the initial user prompt.
    Returns an empty string when no episodes are provided.
    """
    if not episodes:
        return ""

    lines = ["<past_attempts>"]
    for ep in episodes:
        status = "SUCCESS" if ep.success else "FAILED"
        lines.append(
            f"\n## {ep.start_time[:10]} — {status} "
            f"({ep.total_steps} steps, {ep.execution_time:.0f}s)"
        )
        lines.append(f"- category: {ep.category}")
        if not ep.success:
            lines.append(f"- Stopped at step {ep.total_steps} with {ep.error_steps} error steps")

        if ep.tool_usage:
            top_tools = sorted(ep.tool_usage.items(), key=lambda x: x[1], reverse=True)
            top_tools = top_tools[:_TOP_TOOLS_COUNT]
            tool_str = ", ".join(f"{t}×{c}" for t, c in top_tools)
            lines.append(f"- Top tools: {tool_str}")

        if ep.final_result:
            snippet = ep.final_result[:_RESULT_SNIPPET_MAX_CHARS].replace("\n", " ")
            lines.append(f"- Result snippet: {snippet}")

        lines.append(f"- Full trajectory: {ep.trajectory_path}")

    lines.append("\n</past_attempts>")
    return "\n".join(lines)
