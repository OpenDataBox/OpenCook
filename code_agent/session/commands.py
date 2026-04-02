# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Slash command parsing for interactive mode."""

SLASH_COMMANDS = {
    "/help":        "Show help",
    "/new":         "Start a new session",
    "/resume":      "Resume a previous session: /resume [session_id]",
    "/fork":        "Fork the current session",
    "/rename":      "Rename the current session: /rename <title>",
    "/plan":        "Run plan_agent once on the current context; result shown but not written to history",
    "/verify":      "Run test_agent verification once on the current context; result shown but not written to history",
    "/status":      "Show current session status",
    "/clear":       "Clear the screen",
    "/compact":          "Compact session history (Phase 3)",
    "/permissions":      "View permission configuration (Phase 2)",
    "/characterization": "Analyze working directory: file stats, function index, and top-level module dependencies",
}


class SlashCommandParser:
    @staticmethod
    def parse(raw: str) -> tuple[str, list[str]]:
        parts = raw.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]
        return cmd, args
