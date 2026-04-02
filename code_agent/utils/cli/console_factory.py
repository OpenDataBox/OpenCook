# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Console factory for creating different types of CLI consoles."""

import sys

from .cli_console import CLIConsole, ConsoleMode, ConsoleType
from .rich_console import RichCLIConsole
from .simple_console import SimpleCLIConsole


class ConsoleFactory:
    """Factory class for creating CLI console instances."""

    @staticmethod
    def create_console(
        console_type: ConsoleType,
        mode: ConsoleMode = ConsoleMode.RUN,
    ) -> CLIConsole:
        """Create a console instance based on type and mode.

        Used by the `run` command and legacy paths.  The `interactive` command
        should use create_interactive_console() instead.

        Args:
            console_type: Type of console to create (SIMPLE or RICH)
            mode: Console operation mode (RUN or INTERACTIVE)
        Returns:
            CLIConsole instance

        Raises:
            ValueError: If console_type is not supported
        """
        if console_type == ConsoleType.SIMPLE:
            return SimpleCLIConsole(mode=mode)
        elif console_type == ConsoleType.RICH:
            return RichCLIConsole(mode=mode)
        else:
            raise ValueError(
                f"ConsoleType.{console_type.name} is not supported by create_console();"
                " use create_interactive_console() instead."
            )

    @staticmethod
    def create_interactive_console(
        console_type: str = "auto",  # "auto" | "textual" | "chat" | "simple"
    ) -> CLIConsole:
        """Create the best available console for the interactive command.

        Probes availability in order: TextualConsole -> ChatConsole -> SimpleCLIConsole.
        Falls back silently on ImportError; warns when a specific type was requested
        but cannot be satisfied.

        Args:
            console_type: "auto" (probe chain), "textual" (require), "chat" (require),
                or "simple" (force)

        Returns:
            CLIConsole instance
        """
        if console_type == "simple":
            return SimpleCLIConsole(mode=ConsoleMode.INTERACTIVE)

        is_tty = sys.stdin.isatty() and sys.stdout.isatty()

        # Textual tier: alternate-screen TUI, resize-safe.
        if is_tty and console_type in ("auto", "textual"):
            try:
                from code_agent.utils.cli.textual_console import TextualConsole
                return TextualConsole(mode=ConsoleMode.INTERACTIVE)
            except ImportError:
                if console_type == "textual":
                    sys.stderr.write(
                        "Warning: textual not installed, falling back to chat console.\n"
                    )
        elif not is_tty and console_type == "textual":
            sys.stderr.write(
                "Warning: stdout/stdin is not a TTY, falling back to simple console.\n"
            )

        # Chat tier: prompt_toolkit input, Rich scrollback output.
        # Also reached when console_type=="textual" but textual is unavailable,
        # making the warning message ("falling back to chat console") accurate.
        if is_tty and console_type in ("auto", "chat", "textual"):
            try:
                import prompt_toolkit  # noqa: F401
                from code_agent.utils.cli.chat_console import ChatConsole
                return ChatConsole(mode=ConsoleMode.INTERACTIVE)
            except ImportError:
                if console_type == "chat":
                    sys.stderr.write(
                        "Warning: prompt_toolkit not installed,"
                        " falling back to simple console.\n"
                    )
        elif not is_tty and console_type == "chat":
            sys.stderr.write(
                "Warning: stdout/stdin is not a TTY, falling back to simple console.\n"
            )

        return SimpleCLIConsole(mode=ConsoleMode.INTERACTIVE)

    @staticmethod
    def get_recommended_console_type(mode: ConsoleMode) -> ConsoleType:
        """Get the recommended console type for a given mode.

        For INTERACTIVE mode, returns CHAT (probed at runtime by
        create_interactive_console).  The RUN mode branch is unchanged.

        Args:
            mode: Console operation mode

        Returns:
            Recommended console type
        """
        # Chat console is ideal for interactive mode (Phase A: falls back to SIMPLE at runtime)
        if mode == ConsoleMode.INTERACTIVE:
            return ConsoleType.CHAT
        # Simple console works well for run mode (RUN branch unchanged)
        else:
            return ConsoleType.SIMPLE
