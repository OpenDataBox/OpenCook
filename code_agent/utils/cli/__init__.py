# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""CLI console module for OpenCook."""

from .cli_console import CLIConsole, ConsoleMode, ConsoleType, ToolApprovalRequest
from .console_factory import ConsoleFactory
from .rich_console import RichCLIConsole
from .simple_console import SimpleCLIConsole

try:
    from .textual_console import TextualConsole
except ImportError:
    TextualConsole = None  # type: ignore[assignment,misc]

__all__ = [
    "CLIConsole",
    "ConsoleMode",
    "ConsoleType",
    "ToolApprovalRequest",
    "SimpleCLIConsole",
    "RichCLIConsole",
    "ConsoleFactory",
    "TextualConsole",
]
