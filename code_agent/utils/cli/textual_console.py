# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Textual alternate-screen TUI console for OpenCook.

Architecture summary:
- OpenCookApp  : Textual App that owns the terminal (alternate screen)
- TextualConsole: CLIConsole subclass; SessionRunner calls the same interface
  as ChatConsole / SimpleCLIConsole.  cli.py detects run_app() and calls it
  instead of asyncio.run(runner.run()).

On exit, _replay_to_terminal() writes _exit_log (short plain-text lines) to
the main-terminal scrollback so users have a readable history without needing
to open the session transcript file.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import random
import sys
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from typing import override
except ImportError:
    def override(func):
        return func

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input, Static

from code_agent.agent.agent_basics import AgentExecution, AgentStep, AgentStepState
from code_agent.session.commands import SLASH_COMMANDS
from code_agent.utils.cli.cli_console import (
    CLIConsole,
    ConsoleMode,
    ConsoleStep,
    ToolApprovalRequest,
)
from code_agent.utils.demo_mode import (
    DEMO_LIVE_FRAME_DELAY,
    DEMO_PROGRESS_DELAY,
    DEMO_RECORDING_MODE,
    DEMO_TYPEWRITER_DELAY,
)

if TYPE_CHECKING:
    from code_agent.session.runner import SessionRunner
    from code_agent.session.schema import SessionMeta

logger = logging.getLogger(__name__)

# Toggle this constant to switch the CLI brand instantly.
# Supported values: "dbcooker", "opencook"
_CLI_BRAND = "opencook"

# ── Brand color palette (from homepage style.css) ────────────────────────────

_BG = "#060912"
_SURFACE = "#0a1020"
_BORDER = "#1a2438"
_DIM = "#3a4560"
_FG = "#e2e8f0"
_SUBTEXT = "#8896aa"
_CYAN = "#00D4FF"
_PRIMARY = _CYAN
_BLUE = "#2563EB"
_PURPLE = "#7C3AED"
_GREEN = "#4ade80"
_RED = "#f87171"
_AMBER = "#F59E0B"
_ORANGE = "#F97316"
_ORANGE_DARK = "#C2510A"
_MAGENTA = "#bb9af7"
PROJECT_ROOT = Path(__file__).resolve().parents[3]

_BRAND_SPECS: dict[str, dict[str, str]] = {
    "dbcooker": {
        "display": "DBCooker",
        "lead": "DB",
        "accent": "Cooker",
        "mark": "DB",
        "lead_color": _CYAN,
        "accent_color": "#A855F7",
    },
    "opencook": {
        "display": "OpenCook",
        "lead": "Open",
        "accent": "Cook",
        "mark": "OC",
        "lead_color": _CYAN,
        "accent_color": "#A855F7",
    },
}


def _normalize_brand_name(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "").replace("_", "")
    if normalized == "opencook":
        return "opencook"
    return "dbcooker"

_ACTIVE_BRAND_KEY = _normalize_brand_name(_CLI_BRAND)
_ACTIVE_BRAND = _BRAND_SPECS[_ACTIVE_BRAND_KEY]


def _brand_display_name() -> str:
    return _ACTIVE_BRAND["display"]


def _brand_lead_text() -> str:
    return _ACTIVE_BRAND["lead"]


def _brand_accent_text() -> str:
    return _ACTIVE_BRAND["accent"]


def _brand_mark_text() -> str:
    return _ACTIVE_BRAND["mark"]

# ── Mascot definitions ──────────────────────────────────────────────────────

# State-to-status mapping: compact robot faces + accent color
_STATUS_BADGES: dict[AgentStepState, tuple[str, str, str]] = {
    AgentStepState.THINKING: ("<o_o>", _CYAN, "working through it"),
    AgentStepState.CALLING_TOOL: ("<+_+>", _GREEN, "using available tools"),
    AgentStepState.REFLECTING: ("<-_->", _AMBER, "checking the result"),
    AgentStepState.COMPLETED: ("<^_^>", _ORANGE, "ready for the next step"),
    AgentStepState.ERROR: ("<x_x>", _RED, "needs attention"),
}

_HERO_EYE_STATES: dict[str, tuple[tuple[str, str], ...]] = {
    "plan": (("✦", "▶"), ("▶", "✦")),
    "code": (("■", "✦"), ("✦", "■")),
    "test": (("▶", "▶"), ("◀", "◀")),
    "done": (("■", "◀"), ("◀", "■")),
}

_STATUS_STAGE_BY_STATE: dict[AgentStepState, str] = {
    AgentStepState.THINKING: "plan",
    AgentStepState.CALLING_TOOL: "code",
    AgentStepState.REFLECTING: "test",
    AgentStepState.COMPLETED: "done",
    AgentStepState.ERROR: "test",
}

_PUPIL_COLOR = "#020617"

# Color-cycling palette for scanner animation
_PULSE_COLORS = [_CYAN, _GREEN, _RED, _AMBER]

# Scanner width (positions for bouncing dot)
_SCANNER_WIDTH = 10

# ── Tool icon/mode mapping ──────────────────────────────────────────────────

TOOL_ICONS: dict[str, tuple[str, str]] = {
    "bash":                        ("$",    "block"),
    "skill":                       ("->",   "inline"),
    "sequentialthinking":          ("..",   "inline"),
    "sequential_thinking":         ("..",   "inline"),
    "str_replace_based_edit_tool": ("edit", "diff"),
    "task_done":                   ("ok",   "inline"),
    "test_subagent":               ("agt",  "inline"),
    "plan_subagent":               ("agt",  "inline"),
    "database_verify":             ("sql",  "inline"),
    "database_execute":            ("sql",  "block"),
    "json_edit_tool":              ("json", "summary"),
    "_default":                    ("tool", "inline"),
}

# ── Input placeholder rotation ──────────────────────────────────────────────

_PLACEHOLDERS = [
    "  Ask anything you want!",
    "  Make your project personalized!",
    "  Customize your unique feature and logic!",
    "  Try: /help for commands",
    # "  Try: optimize this slow query",
    # "  Try: add an index for better performance",
]

# Animation knobs: tweak these to change perceived speed / sparkle.
# TEMP(demo): the faster values are guarded so the normal pacing comes back by
# flipping DEMO_RECORDING_MODE to False in code_agent/utils/demo_mode.py.
_LIVE_FRAME_DELAY = DEMO_LIVE_FRAME_DELAY if DEMO_RECORDING_MODE else 0.04
_PROGRESS_FRAME_DELAY = DEMO_PROGRESS_DELAY if DEMO_RECORDING_MODE else _LIVE_FRAME_DELAY
_STATUS_FRAME_INTERVAL = _LIVE_FRAME_DELAY
_STATUS_MARKER_FRAMES = ("◐", "◓", "◑", "◒")
_PROGRESS_MARKER_FRAMES = ("◴", "◷", "◶", "◵")
_HERO_FRAME_INTERVAL = 0.16
_TYPEWRITER_TYPE_CHARS = 3
_TYPEWRITER_ERASE_CHARS = 4
_TYPEWRITER_HOLD_TICKS = 2
_TYPEWRITER_PAUSE_TICKS = 1
_TYPEWRITER_BLINK_PERIOD = 8
_LOG_MARKDOWN_CHAR_LIMIT = 12000

_STATUS_TYPED_PHRASES: dict[AgentStepState, tuple[str, ...]] = {
    AgentStepState.THINKING: (
        "working through the request",
        "checking nearby context",
        "shaping the response",
        "probing for useful clues",
    ),
    AgentStepState.CALLING_TOOL: (
        "running the next command",
        "waiting on tool output",
        "reading the latest result",
        "folding results back in",
    ),
    AgentStepState.REFLECTING: (
        "checking the result",
        "re-reading the output",
        "looking for edge cases",
        "tightening the answer",
    ),
    AgentStepState.COMPLETED: ("turn complete",),
    AgentStepState.ERROR: ("needs attention", "backtracking the last step"),
}

_CHARACTERIZATION_SCAN_PHRASES = (
    "counting files",
    "measuring text lines",
    "reading function index",
    "ranking busy files",
    "tracing python imports",
)

_CHARACTERIZATION_INDEX_PHRASES = (
    "resolving declaration",
    "walking call edges",
    "collecting dependencies",
)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _blend_hex(left: str, right: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    lr, lg, lb = _hex_to_rgb(left)
    rr, rg, rb = _hex_to_rgb(right)
    return _rgb_to_hex(
        (
            round(lr + (rr - lr) * t),
            round(lg + (rg - lg) * t),
            round(lb + (rb - lb) * t),
        )
    )


def _brand_gradient_color(t: float) -> str:
    if t <= 0.55:
        return _blend_hex(_CYAN, _BLUE, t / 0.55 if 0.55 else 0.0)
    return _blend_hex(_BLUE, _PURPLE, (t - 0.55) / 0.45 if 0.45 else 1.0)


def _hero_eye_pair(stage: str, frame: int, *, phase: int = 0) -> tuple[str, str]:
    states = _HERO_EYE_STATES.get(stage, _HERO_EYE_STATES["plan"])
    return states[((frame // 2) + phase) % len(states)]


def _animated_brand_color(t: float, frame: int, *, phase: int = 0) -> str:
    base = _brand_gradient_color(t)
    sweep = ((frame * 2 + phase) % 28) / 27
    delta = abs(t - sweep)
    delta = min(delta, 1.0 - delta)
    glow = max(0.0, 1.0 - (delta / 0.18))
    return _blend_hex(base, _FG, 0.08 + glow * 0.26)


def _animated_gradient_text(
    content: str,
    *,
    frame: int,
    start_t: float = 0.0,
    end_t: float = 1.0,
    bold: bool = True,
    phase: int = 0,
) -> Text:
    text = Text(no_wrap=True, overflow="crop")
    visible_total = sum(1 for char in content if char != " ")
    visible_index = 0
    span = end_t - start_t
    for char in content:
        if char == " ":
            text.append(char)
            continue
        frac = 0.0 if visible_total <= 1 else visible_index / (visible_total - 1)
        tone = start_t + span * frac
        style = f"{'bold ' if bold else ''}{_animated_brand_color(tone, frame, phase=phase + visible_index)}"
        text.append(char, style=style)
        visible_index += 1
    return text


def _append_inline_brand(text: Text, *, bold: bool = True) -> None:
    weight = "bold " if bold else ""
    text.append(_brand_lead_text(), style=f"{weight}{_ACTIVE_BRAND['lead_color']}")
    text.append(_brand_accent_text(), style=f"{weight}{_ACTIVE_BRAND['accent_color']}")


def _append_centered_brand_title(text: Text, width: int) -> Text:
    inner_width = max(18, width - 6)
    if _ACTIVE_BRAND_KEY == "opencook":
        plain = "Cook Your Personalized Projects!" if inner_width >= 26 else ""
    else:
        prefix = "Welcome to " if inner_width >= 26 else ""
        plain = f"{prefix}{_brand_display_name()}"
    left_pad = max(0, (inner_width - len(plain)) // 2)
    right_pad = max(0, inner_width - len(plain) - left_pad)
    if left_pad:
        text.append(" " * left_pad)
    if _ACTIVE_BRAND_KEY == "opencook":
        if plain:
            text.append(plain, style=f"bold {_PRIMARY}")
    else:
        if prefix:
            text.append(prefix, style=f"bold {_PRIMARY}")
        _append_inline_brand(text)
    if right_pad:
        text.append(" " * right_pad)
    return text


def _center_brand_line(line: Text, width: int) -> Text:
    centered = Text(no_wrap=True, overflow="crop")
    pad_left = max(0, (width - len(line.plain)) // 2)
    pad_right = max(0, width - len(line.plain) - pad_left)
    if pad_left:
        centered.append(" " * pad_left)
    centered.append_text(line)
    if pad_right:
        centered.append(" " * pad_right)
    return centered


def _build_alternate_brand_wordmark(
    width: int,
    *,
    frame: int,
    eyebrow: Text,
    active_title: str,
    active_color: str,
) -> Group:
    inner_width = max(28, width - 2)

    if inner_width < 58:
        compact = Text(no_wrap=True, overflow="crop")
        compact.append(_brand_lead_text(), style=f"bold {_ACTIVE_BRAND['lead_color']}")
        compact.append(_brand_accent_text(), style=f"bold {_blend_hex(_BLUE, _PURPLE, 0.58)}")
        return Group(compact, eyebrow)

    op_glyphs = {
        "O": [
            " ███████████  ",
            "██         ██ ",
            "██         ██ ",
            "██         ██ ",
            "██         ██ ",
            "██         ██ ",
            "██         ██ ",
            "██         ██ ",
            "██         ██ ",
            " ███████████  ",
        ],
        "p": [
            "",
            "",
            "",
            "",
            "",
            "███████",
            "██    ██",
            "███████",
            "██",
            "██",
        ],
    }
    e_glyph = [
        "▄███████▄  ",
        "██      ██ ",
        "█████████  ",
        "██         ",
        "▀███████▀  ",
    ]
    lower_glyphs = {
        "n": [
            "▄███████▄ ",
            "██     ██ ",
            "██     ██ ",
            "██     ██ ",
            "██     ██ ",
        ],
        "C": [
            " █████████ ",
            "██       ██",
            "██         ",
            "██         ",
            "██         ",
            "██         ",
            "██         ",
            "██       █ ",
            " █████████ ",
        ],
        "o": [
            " ▟███▙ ",
            "██   ██",
            "██   ██",
            " ▜███▛ ",
        ],
        "k": [
            "██  ██  ",
            "██ ██   ",
            "████    ",
            "██ ▜██  ",
        ],
    }
    lower_specs = [
        ("n", _blend_hex(_BLUE, _CYAN, 0.55)),
        ("C", _blend_hex(_PURPLE, "#8B5CF6", 0.10)),
        ("o", _blend_hex(_PURPLE, "#8B5CF6", 0.28)),
        ("o", _blend_hex(_PURPLE, "#8B5CF6", 0.48)),
        ("k", _blend_hex(_PURPLE, "#A855F7", 0.72)),
    ]
    lower_glyph_height = max(len(glyph) for glyph in lower_glyphs.values())

    def _fixed_row(text: Text, row_width: int) -> Text:
        line = Text(no_wrap=True, overflow="crop")
        line.append_text(text)
        pad = max(0, row_width - len(text.plain))
        if pad:
            line.append(" " * pad)
        return line

    def _blank_row(row_width: int) -> Text:
        return Text(" " * row_width, no_wrap=True, overflow="crop")

    def _solid_cells(content: str, style: str | None) -> list[tuple[str, str | None]]:
        return [(char, style if char != " " else None) for char in content]

    def _cells_to_text(cells: list[tuple[str, str | None]]) -> Text:
        line = Text(no_wrap=True, overflow="crop")
        if not cells:
            return line
        current_style = cells[0][1]
        buffer = ""
        for char, style in cells:
            if style == current_style:
                buffer += char
            else:
                line.append(buffer, style=current_style)
                buffer = char
                current_style = style
        if buffer:
            line.append(buffer, style=current_style)
        return line

    outline = active_color
    hat_top = _ORANGE
    hat_brim = _ORANGE_DARK
    foot_fill = "#8b5a2b"
    fill_shades = {
        "plan": ("#16384d", "#21506d"),
        "code": ("#173f2b", "#22603f"),
        "test": ("#472634", "#63374b"),
        "done": ("#4d3a16", "#6a531f"),
    }
    fill_light, fill_dark = fill_shades.get(active_title.lower(), ("#16384d", "#21506d"))
    mouth_symbol = {
        "plan": "o",
        "code": "=",
        "test": "~",
        "done": "w",
    }.get(active_title.lower(), ".")
    pupil_color = "#020617"

    def _animated_block_lens(phase: int = 0) -> tuple[str, str]:
        states = [("Tp", "qq"), ("pT", "qq")]
        top, bottom = states[((frame // 2) + phase) % len(states)]
        return top, bottom

    def _animated_eye_shape(phase: int = 0) -> tuple[str, str]:
        states_by_stage = {
            "plan": [("✦", "▶"), ("▶", "✦")],
            "code": [("■", "✦"), ("✦", "■")],
            "test": [("▶", "▶"), ("◀", "◀")],
            "done": [("■", "◀"), ("◀", "■")],
        }
        states = states_by_stage.get(active_title.lower(), [("✦", "▶"), ("▶", "✦")])
        return states[((frame // 2) + phase) % len(states)]

    def _animated_blush(phase: int = 0) -> tuple[str, str]:
        states = [
            ("━", "#d9778b"),
            ("━", "#f472b6"),
            ("━", "#fb7185"),
            ("━", "#f472b6"),
        ]
        return states[((frame // 2) + phase) % len(states)]

    def _animated_mouth(phase: int = 0) -> str:
        states_by_stage = {
            "plan": [".", "·", ".", "·"],
            "code": ["=", "≡", "=", "≡"],
            "test": ["~", "≈", "~", "≈"],
            "done": ["w", "-", "w", "-"],
        }
        states = states_by_stage.get(active_title.lower(), [mouth_symbol] * 4)
        return states[((frame // 2) + phase) % len(states)]

    def _animated_cheek_row(phase: int = 0) -> str:
        states = [
            "   │ R M R  │ ",
            "   │  R M R │ ",
        ]
        return states[((frame // 2) + phase) % len(states)]

    def _mascot_body_cells(pattern: str) -> list[tuple[str, str | None]]:
        cells: list[tuple[str, str | None]] = []
        blush_char, blush_color = _animated_blush()
        animated_mouth = _animated_mouth()
        eye_fill_symbol, eye_pupil_symbol = _animated_eye_shape()
        for char in pattern:
            if char in {"E", "e", "T", "B"}:
                cells.append((eye_fill_symbol, f"bold {outline}"))
            elif char == "M":
                cells.append((animated_mouth, f"bold {outline}"))
            elif char in {"p", "q"}:
                cells.append((eye_pupil_symbol, f"bold {pupil_color}"))
            elif char == "R":
                cells.append((blush_char, f"bold {blush_color}"))
            elif char == "F":
                cells.append(("■", f"bold {outline}"))
            elif char == "█":
                cells.append(("█", f"bold {foot_fill}"))
            elif char in {"╱", "╲", "│", "╰", "╯", "─", "┌", "┐", "└", "┘"}:
                cells.append((char, outline))
            else:
                cells.append((char, None))
        return cells

    op_width = len(op_glyphs["O"][0]) + max(len(row) for row in op_glyphs["p"])
    op_lines: list[Text] = []
    op_height = max(len(op_glyphs["O"]), len(op_glyphs["p"]))
    for row in range(op_height):
        line = Text(no_wrap=True, overflow="crop")
        if row < len(op_glyphs["O"]):
            line.append_text(
                _animated_gradient_text(
                    op_glyphs["O"][row],
                    frame=frame,
                    start_t=0.00,
                    end_t=0.05,
                    phase=row,
                )
            )
        else:
            line.append(" " * len(op_glyphs["O"][0]))
        line.append("")
        if row < len(op_glyphs["p"]):
            line.append_text(
                _animated_gradient_text(
                    op_glyphs["p"][row],
                    frame=frame,
                    start_t=0.03,
                    end_t=0.09,
                    phase=row + 4,
                )
            )
        else:
            line.append(" " * max(len(text) for text in op_glyphs["p"]))
        op_lines.append(_fixed_row(line, op_width))
    op_vertical_offset = 0

    e_width = max(len(row) for row in e_glyph)

    lower_word_width = sum(len(lower_glyphs[ch][0]) for ch, _ in lower_specs) + (len(lower_specs) - 1)
    lens_top, _ = _animated_block_lens()
    mascot_patterns = [
        _solid_cells("     ▄▄▄▄▄▄     ", hat_top),
        _solid_cells("   ██████████   ", hat_brim),
        _mascot_body_cells(f"   ╱  {lens_top}{lens_top}  ╲ "),
        _mascot_body_cells(_animated_cheek_row()),
        _mascot_body_cells("   ╰─██──██─╯   "),
    ]
    mascot_width = max(len(row) for row in mascot_patterns)

    spoon_style = f"bold {_blend_hex(_ORANGE, _FG, 0.45)}"
    spoon_rows = [
        _solid_cells("◯ ", spoon_style),
        _solid_cells(" ╲", spoon_style),
    ]

    pot_style = "bold #8fb6d9"
    steam_style = "bold #ffffff"
    pot_fill_style = f"bold {_blend_hex(_AMBER, _FG, 0.12)}"
    steam_frames = [
        "  ∿  ∿  ",
        " ∿  ∿   ",
        "   ∿  ∿ ",
        "  ~  ~  ",
    ]
    pot_fill_frames = [
        "▒░▒▓",
        "░▒▓▒",
        "▒▓▒░",
        "▓▒░▒",
    ]
    steam_row = _solid_cells(steam_frames[(frame // 2) % len(steam_frames)], steam_style)
    pot_fill_row = (
        _solid_cells("□┤", pot_style)
        + _solid_cells(pot_fill_frames[(frame // 2) % len(pot_fill_frames)], pot_fill_style)
        + _solid_cells("├□", pot_style)
    )
    pot_rows = [
        steam_row,
        _solid_cells("  ▁▄▄▁  ", pot_style),
        pot_fill_row,
        _solid_cells(" ╰─▄▄─╯ ", pot_style),
    ]
    pot_width = max(len(row) for row in pot_rows)
    lower_start = len(mascot_patterns) - 1
    rest_left_pad = max(0, (mascot_width - e_width) // 2 - 2)
    e_x = rest_left_pad
    lower_x = e_x + e_width
    mascot_y = 0
    pot_y = lower_start + 1 - len(pot_rows)
    glyph_vertical_offsets = {
        "n": 1,
        "C": -3,
        "o": 2,
        "k": 2,
    }
    mascot_x = max(0, e_x + (e_width - mascot_width) // 2 - 14)
    spoon_x = max(0, mascot_x - 1)
    n_width = len(lower_glyphs["n"][0])
    pot_x = lower_x + max(0, (n_width - pot_width) // 2)
    n_to_c_gap = 1
    glyph_x_positions: list[int] = []
    cursor_x = lower_x
    for index, (char, _color) in enumerate(lower_specs):
        glyph_x_positions.append(cursor_x)
        cursor_x += len(lower_glyphs[char][0])
        if index < len(lower_specs) - 1:
            if index == 0:
                cursor_x += n_to_c_gap
            elif index == 1:
                cursor_x += 0
            else:
                cursor_x += 2
    rest_width = max(
        e_x + e_width,
        cursor_x,
        mascot_x + mascot_width,
        pot_x + pot_width,
    ) + 2
    e_start_y = lower_start + 1
    rest_height = max(
        e_start_y + len(e_glyph),
        lower_start + lower_glyph_height,
        mascot_y + len(mascot_patterns),
        pot_y + len(pot_rows),
    )

    canvas_chars = [[" " for _ in range(rest_width)] for _ in range(rest_height)]
    canvas_styles: list[list[str | None]] = [[None for _ in range(rest_width)] for _ in range(rest_height)]

    def _place_row(y: int, x: int, cells: list[tuple[str, str | None]]) -> None:
        if y < 0 or y >= rest_height:
            return
        for offset, (char, style) in enumerate(cells):
            px = x + offset
            if px < 0 or px >= rest_width or char == " ":
                continue
            canvas_chars[y][px] = char
            canvas_styles[y][px] = style

    for row_index, row_text in enumerate(e_glyph):
        if not row_text:
            continue
        row_color = _blend_hex(_BLUE, _CYAN, 0.10 + (((frame + row_index) % 6) / 5) * 0.18)
        _place_row(e_start_y + row_index, e_x, _solid_cells(row_text, f"bold {row_color}"))

    for glyph_index, (char, color) in enumerate(lower_specs):
        glyph_rows = lower_glyphs[char]
        glyph_x = glyph_x_positions[glyph_index]
        glyph_y = lower_start + glyph_vertical_offsets.get(char, 0)
        for row_index, glyph_row in enumerate(glyph_rows):
            glow = ((frame + glyph_index * 2 + row_index) % 8) / 7
            animated_color = _blend_hex(color, _FG, 0.06 + glow * 0.16)
            _place_row(glyph_y + row_index, glyph_x, _solid_cells(glyph_row, f"bold {animated_color}"))
    for row_index, cells in enumerate(mascot_patterns):
        _place_row(mascot_y + row_index, mascot_x, cells)
    for row_index, cells in enumerate(spoon_rows):
        _place_row(1 + row_index, spoon_x, cells)
    for row_index, cells in enumerate(pot_rows):
        _place_row(pot_y + row_index, pot_x, cells)

    rest_lines: list[Text] = []
    for row_index in range(rest_height):
        row_cells = list(zip(canvas_chars[row_index], canvas_styles[row_index]))
        rest_lines.append(_cells_to_text(row_cells))

    total_rows = max(rest_height, len(op_lines) + op_vertical_offset)
    blank_op = _blank_row(op_width)
    blank_rest = _blank_row(rest_width)
    brand_rows: list[Text] = []
    for row in range(total_rows):
        line = Text(no_wrap=True, overflow="crop")
        if op_vertical_offset <= row < op_vertical_offset + len(op_lines):
            line.append_text(op_lines[row - op_vertical_offset])
        else:
            line.append_text(blank_op)
        line.append(" ")
        line.append(" ")
        line.append_text(rest_lines[row] if row < len(rest_lines) else blank_rest)
        brand_rows.append(line)

    subtitle = Text()
    subtitle.append(_brand_lead_text(), style=f"bold {_ACTIVE_BRAND['lead_color']}")
    subtitle.append(_brand_accent_text(), style=f"bold {_blend_hex(_PURPLE, '#A855F7', 0.35)}")
    while len(brand_rows) > 1 and not brand_rows[-1].plain.strip():
        brand_rows.pop()
    return Group(*brand_rows, Text(""), subtitle, eyebrow)



def _tool_icon_mode(tool_name: str) -> tuple[str, str]:
    """Return (icon, display_mode) for a tool name."""
    return TOOL_ICONS.get(tool_name, TOOL_ICONS["_default"])


def _is_sequential_thinking_tool(tool_name: str) -> bool:
    return tool_name in {"sequentialthinking", "sequential_thinking"}


def _tool_accent(tool_name: str) -> str:
    """Return accent color for a tool type."""
    if tool_name in {"bash", "database_execute"}:
        return _CYAN
    if tool_name in {"str_replace_based_edit_tool", "json_edit_tool"}:
        return _AMBER
    if "subagent" in tool_name:
        return _MAGENTA
    return _GREEN


def _tool_accent_bg(tool_name: str) -> str:
    """Subtle dark background tint for tool details."""
    if tool_name in {"bash", "database_execute"}:
        return "#081926"
    if tool_name in {"str_replace_based_edit_tool", "json_edit_tool"}:
        return "#25150a"
    if "subagent" in tool_name:
        return "#0f1830"
    return "#081926"


def _ellipsize(value: str, limit: int = 96) -> str:
    cleaned = value.replace("\r", " ").replace("\n", " ").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _truncate_markdown_for_log(content: str, limit: int = _LOG_MARKDOWN_CHAR_LIMIT) -> str:
    text = content.strip()
    if len(text) <= limit:
        return text

    lines = text.splitlines()
    visible_lines: list[str] = []
    visible_chars = 0

    for line in lines:
        separator = 1 if visible_lines else 0
        projected = visible_chars + separator + len(line)
        if projected <= limit:
            visible_lines.append(line)
            visible_chars = projected
            continue

        remaining = limit - visible_chars - separator
        if remaining > 0:
            visible_lines.append(line[:remaining].rstrip())
            visible_chars += separator + len(visible_lines[-1])
        break

    visible_text = "\n".join(visible_lines).rstrip()
    hidden_chars = max(0, len(text) - len(visible_text))
    hidden_lines = max(0, len(lines) - len(visible_lines))

    if hidden_lines > 0:
        suffix = f"\n\n... [truncated {hidden_lines} more lines, {hidden_chars} chars total]"
    else:
        suffix = f"\n\n... [truncated {hidden_chars} chars]"
    return visible_text + suffix


def _tool_summary(tool_name: str, arguments: dict) -> str:
    """Extract a compact summary string from tool arguments."""
    if _is_sequential_thinking_tool(tool_name):
        return ""
    if tool_name == "str_replace_based_edit_tool":
        command = str(arguments.get("command", "") or "").replace("\r", " ").replace("\n", " ").strip()
        path = str(arguments.get("path", "") or "").replace("\r", " ").replace("\n", " ").strip()
        view_range = arguments.get("view_range")
        insert_line = arguments.get("insert_line")
        if command == "view" and path:
            if isinstance(view_range, list) and len(view_range) == 2:
                end = "end" if view_range[1] == -1 else str(view_range[1])
                joined = f"view {path}:{view_range[0]}-{end}"
            else:
                joined = f"view {path}"
            return joined[:96] if len(joined) <= 96 else joined[:93] + "..."
        if command == "insert" and path:
            joined = f"insert {path}"
            if isinstance(insert_line, int):
                joined += f" @{insert_line}"
            return joined[:96] if len(joined) <= 96 else joined[:93] + "..."
        if path:
            return path[:96] if len(path) <= 96 else path[:93] + "..."
        if command:
            return command[:96] if len(command) <= 96 else command[:93] + "..."
    for key in ("command", "sql", "task", "path", "json_path", "operation"):
        val = arguments.get(key)
        if val:
            s = str(val).replace("\r", " ").replace("\n", " ").strip()
            return s[:96] if len(s) <= 96 else s[:93] + "..."
    if not arguments:
        return ""
    first_key = next(iter(arguments))
    s = str(arguments[first_key]).replace("\n", " ").strip()
    return s[:96] if len(s) <= 96 else s[:93] + "..."


# ── ChatLog widget ──────────────────────────────────────────────────────────


class ChatLog(Static):
    """Accumulating log widget that renders all items via Rich Group.

    Used inside VerticalScroll so the Input naturally follows the content
    instead of being pinned to the screen bottom.
    """

    DEFAULT_CSS = """
    ChatLog {
        height: auto;
        width: 100%;
        padding: 0 1;
        background: #0d1120;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._items: list = []

    def write(self, item) -> None:
        """Append a renderable item and trigger re-render."""
        self._items.append(item)
        if len(self._items) > 300:
            self._items = self._items[-200:]
        self.refresh(layout=True)

    def render(self):
        if not self._items:
            return ""
        return Group(*self._items)

    def clear(self) -> None:
        self._items.clear()
        self.refresh(layout=True)

    def item_count(self) -> int:
        return len(self._items)

    def remove_last(self, count: int) -> None:
        if count > 0:
            del self._items[-count:]
            self.refresh(layout=True)

    def remove_range(self, start: int, count: int) -> None:
        if count <= 0:
            return
        start = max(0, start)
        end = min(len(self._items), start + count)
        if start >= end:
            return
        del self._items[start:end]
        self.refresh(layout=True)


class _AnimatedToolCallBlock:
    """Re-render running tool calls so their inline effects animate in place."""

    def __init__(
        self,
        owner,
        tool_name: str,
        arguments: dict,
        result: str | None,
        *,
        success: bool,
    ) -> None:
        self._owner = owner
        self._tool_name = tool_name
        self._arguments = arguments
        self._result = result
        self._success = success

    def __rich_console__(self, console, options):
        yield self._owner._build_tool_call_block(
            self._tool_name,
            self._arguments,
            self._result,
            success=self._success,
            active=True,
        )


# ── OpenCookApp ─────────────────────────────────────────────────────────────


class OpenCookApp(App[None]):
    """Full-screen Textual TUI with OpenCook brand theme.

    Layout: header | VerticalScroll(ChatLog + status + cmd_popup + Input) | footer

    The Input lives inside the scroll area so it follows the content naturally,
    rather than being pinned to the bottom of the screen.
    """

    CSS_PATH = "textual_console.tcss"

    def __init__(self, console: "TextualConsole") -> None:
        super().__init__()
        self._tui_console = console
        self._status_empty: bool = True
        self._quit_in_progress: bool = False
        self._placeholder_idx: int = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        with VerticalScroll(id="chat_area"):
            yield Static("", id="hero")
            yield ChatLog(id="log")
            yield Static("", id="status")
            yield Static("", id="approval_panel")
            yield Input(id="input", placeholder=_PLACEHOLDERS[0])
            yield Static("", id="cmd_popup")
        yield Static("", id="footer")

    async def on_mount(self) -> None:
        self.call_after_refresh(self.query_one(Input).focus)
        self.set_interval(_STATUS_FRAME_INTERVAL, self._tick_spinner)
        self.set_interval(5.0, self._rotate_placeholder)
        self.set_interval(_HERO_FRAME_INTERVAL, self._tui_console.tick_preview_hero)
        self._tui_console._supports_report_hotkey = True
        self._runner_task: asyncio.Task = asyncio.create_task(self._run_session())

    async def _run_session(self) -> None:
        try:
            await self._tui_console._runner.run()  # type: ignore[union-attr]
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Session runner failed")
        finally:
            self.exit()

    # ── Ctrl+C handling ──────────────────────────────────────────────────────

    async def action_quit(self) -> None:
        await self.action_request_quit()

    async def action_request_quit(self) -> None:
        if self._quit_in_progress:
            return
        self._quit_in_progress = True
        try:
            await self._handle_quit_logic()
        finally:
            self._quit_in_progress = False

    async def _handle_quit_logic(self) -> None:
        c = self._tui_console
        now = asyncio.get_event_loop().time()

        if c._approval_mode:
            c._approval_mode = False
            c._deny_reason_mode = False
            self._hide_approval_panel()
            self.query_one(Input).placeholder = c._get_prompt_text()
            self.query_one(Input).remove_class("deny-mode")
            await c._approval_queue.put("n")
            return

        if c._turn_running:
            if not c._interrupt_requested:
                c._interrupt_requested = True
                c._interrupt_time = now
                self.write_log(Text(
                    "  Interrupted. Press Ctrl+C again within 3s to force exit.",
                    style=_AMBER,
                ))
                _turn_task = getattr(c._runner, "_turn_task", None)
                if _turn_task and not _turn_task.done():
                    _turn_task.cancel()
                return
            if now - c._interrupt_time < 3.0:
                await self._force_exit()
                return
            c._interrupt_requested = True
            c._interrupt_time = now
            _turn_task = getattr(c._runner, "_turn_task", None)
            if _turn_task and not _turn_task.done():
                _turn_task.cancel()
            self.write_log(Text(
                "  Interrupted. Press Ctrl+C again within 3s to force exit.",
                style=_AMBER,
            ))
        else:
            await self._force_exit()

    async def on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+c":
            event.stop()
            await self.action_request_quit()
            return
        c = self._tui_console
        # ── Approval panel navigation (selection mode only) ───────────────
        if c._approval_mode and not c._deny_reason_mode:
            if event.key in ("up", "down"):
                event.stop()
                delta = -1 if event.key == "up" else 1
                c._selected_approval = (c._selected_approval + delta) % 4
                self._render_approval_panel()
                return
        # ── Input history (Up/Down when not in approval mode) ─────────────
        if not c._approval_mode and event.key in ("up", "down"):
            history = c._input_history
            if history:
                event.stop()
                inp = self.query_one(Input)
                if event.key == "up":
                    if c._history_idx == -1:
                        c._history_draft = inp.value
                    if c._history_idx < len(history) - 1:
                        c._history_idx += 1
                    inp.value = history[-(c._history_idx + 1)]
                    inp.cursor_position = len(inp.value)
                else:
                    if c._history_idx > 0:
                        c._history_idx -= 1
                        inp.value = history[-(c._history_idx + 1)]
                    elif c._history_idx == 0:
                        c._history_idx = -1
                        inp.value = c._history_draft
                    inp.cursor_position = len(inp.value)
                return
        # ── Tab autocomplete for slash commands ───────────────────────────
        if event.key == "tab":
            event.stop()
            event.prevent_default()
            inp = self.query_one(Input)
            val = inp.value
            if val.startswith("/"):
                prefix = val.lower()
                matches = [k for k in SLASH_COMMANDS if k.startswith(prefix)]
                if len(matches) == 1:
                    inp.value = matches[0] + " "
                    inp.cursor_position = len(inp.value)
                elif len(matches) > 1:
                    import os as _os
                    common = _os.path.commonprefix(matches)
                    if len(common) > len(prefix):
                        inp.value = common
                        inp.cursor_position = len(common)
            inp.focus()
            return
        if event.key == "o":
            event.stop()
            self._tui_console._open_complete_trajectory()

    async def _force_exit(self) -> None:
        if self._runner_task and not self._runner_task.done():
            self._runner_task.cancel()
            try:
                await asyncio.wait_for(self._runner_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self.exit()

    # ── Approval panel ───────────────────────────────────────────────────────

    def _render_approval_panel(self) -> None:
        """Render the 4-option approval list, highlighting the current selection."""
        c = self._tui_console
        specs = [
            ("Yes, approve this tool call once",                 _GREEN),
            ("Yes, approve this tool for the rest of this turn", _CYAN),
            ("Yes, approve all remaining tools this turn",       _AMBER),
            ("No, deny — tell the agent why (optional)",         _RED),
        ]
        text = Text()
        text.append("\n")
        if c._deny_reason_mode:
            color = _RED
            label = "No, deny — tell the agent why (optional)"
            prefix = "  ◉ "
            label_padded = label.ljust(max(0, 72 - len(prefix)))
            text.append(prefix, style=f"bold {color} on #111827")
            text.append(label_padded + "\n", style=f"bold {_FG} on #111827")
            try:
                current = self.query_one(Input).value
            except Exception:
                current = ""
            text.append("    › ", style=f"bold {color}")
            text.append(current if current else " ", style=_FG)
            text.append("_\n", style=f"bold {color}")
        else:
            for i, (label, color) in enumerate(specs):
                if i == c._selected_approval:
                    prefix = "  ◉ "
                    label_padded = label.ljust(max(0, 72 - len(prefix)))
                    text.append(prefix, style=f"bold {color} on #111827")
                    text.append(label_padded + "\n", style=f"bold {_FG} on #111827")
                else:
                    text.append("  ○ ", style=_DIM)
                    text.append(label + "\n", style=_DIM)
        text.append("\n")
        panel = self.query_one("#approval_panel", Static)
        panel.update(text)
        panel.add_class("visible")
        self.call_after_refresh(self._scroll_to_end)

    def _hide_approval_panel(self) -> None:
        self.query_one("#approval_panel", Static).remove_class("visible")

    async def _submit_approval_choice(self, raw_value: str = "") -> None:
        c = self._tui_console
        inp = self.query_one(Input)
        if c._deny_reason_mode:
            reason = raw_value.strip()
            c._approval_mode = False
            c._deny_reason_mode = False
            self._hide_approval_panel()
            inp.placeholder = c._get_prompt_text()
            inp.remove_class("deny-mode")
            inp.value = ""
            payload = f"n:{reason}" if reason else "n"
            await c._approval_queue.put(payload)
            rec = Text()
            rec.append("  ✓", style=f"bold {_RED}")
            deny_display = f"Denied: {reason}" if reason else "Denied"
            rec.append(deny_display, style=_DIM)
            c._write(rec)
            inp.focus()
            return

        choices = ["y", "t", "s", "n"]
        choice = choices[c._selected_approval]
        if choice == "n":
            c._deny_reason_mode = True
            inp.placeholder = "  Deny reason (optional, Enter to confirm): "
            inp.add_class("deny-mode")
            inp.focus()
            self._render_approval_panel()
            return

        c._approval_mode = False
        self._hide_approval_panel()
        inp.placeholder = c._get_prompt_text()
        inp.remove_class("deny-mode")
        inp.value = ""
        await c._approval_queue.put(choice)
        labels = {
            "y": "Approved (once)",
            "t": "Approved (this tool, rest of turn)",
            "s": "Approved (all remaining this turn)",
        }
        rec = Text()
        rec.append("  ✓", style=f"bold {_GREEN}")
        rec.append(labels.get(choice, "Approved"), style=_DIM)
        c._write(rec)
        inp.focus()

    # ── Scanner animation ────────────────────────────────────────────────────

    async def _tick_spinner(self) -> None:
        c = self._tui_console
        status = self.query_one("#status", Static)
        if c._spinning:
            c._spin_idx += 1
            text = c._build_scanner_text(c._spin_idx)
            status.update(text)
            self._status_empty = False
        elif not self._status_empty:
            status.update("")
            self._status_empty = True

    # ── Placeholder rotation ─────────────────────────────────────────────────

    async def _rotate_placeholder(self) -> None:
        c = self._tui_console
        if c._approval_mode or c._turn_running:
            return
        self._placeholder_idx = (self._placeholder_idx + 1) % len(_PLACEHOLDERS)
        self.query_one(Input).placeholder = _PLACEHOLDERS[self._placeholder_idx]

    # ── Slash command popup ──────────────────────────────────────────────────

    async def on_input_changed(self, event: Input.Changed) -> None:
        c = self._tui_console
        if c._deny_reason_mode:
            self._render_approval_panel()
            return
        popup = self.query_one("#cmd_popup", Static)
        val = event.value.strip()
        if val.startswith("/"):
            prefix = val.lower()
            matches = {k: v for k, v in SLASH_COMMANDS.items() if k.startswith(prefix)}
            if matches:
                text = Text()
                for name, desc in matches.items():
                    text.append(f"  {name:15s}", style=f"bold {_CYAN}")
                    text.append(f" {desc}\n", style=_DIM)
                popup.update(text)
                popup.add_class("visible")
                self.call_after_refresh(self._scroll_to_end)
            else:
                popup.remove_class("visible")
        else:
            popup.remove_class("visible")

    # ── Input submission ─────────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value
        event.input.value = ""
        # Hide command popup on submit
        self.query_one("#cmd_popup", Static).remove_class("visible")
        c = self._tui_console
        if c._approval_mode:
            await self._submit_approval_choice(value)
            return
        else:
            if value.strip():
                c._input_history.append(value)
            c._history_idx = -1
            c._history_draft = ""
            await c._input_queue.put(value)

    # ── Resize ───────────────────────────────────────────────────────────────

    async def on_resize(self, event: events.Resize) -> None:
        self._tui_console._update_header()
        self._tui_console._update_footer()
        if self._tui_console._splash_done:
            self._tui_console._update_hero(
                self._tui_console._build_welcome_hero(self._tui_console._hero_frame)
            )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def write_log(self, renderable) -> None:
        """Write to the chat log and scroll to keep input visible."""
        self.query_one("#log", ChatLog).write(renderable)
        self.call_after_refresh(self._scroll_to_end)

    def trim_log(self, count: int) -> None:
        """Remove the last `count` items from the chat log (for live preview replace)."""
        self.query_one("#log", ChatLog).remove_last(count)

    def _scroll_to_end(self) -> None:
        """Scroll the chat area to the bottom."""
        self.query_one("#chat_area", VerticalScroll).scroll_end(animate=False)


# ── TextualConsole ──────────────────────────────────────────────────────────


class TextualConsole(CLIConsole):
    """CLIConsole backed by a Textual full-screen App.

    cli.py detects the presence of run_app() and calls it instead of
    asyncio.run(runner.run()), so this console drives both the TUI lifecycle
    and the SessionRunner loop from a single asyncio event loop.
    """

    def __init__(
        self,
        mode: ConsoleMode = ConsoleMode.INTERACTIVE,
    ):
        super().__init__(mode)

        # Plumbing
        self._runner: SessionRunner | None = None
        self._app: OpenCookApp | None = None
        self._input_queue: asyncio.Queue[str | None]
        self._selected_approval: int = 0    # 0=y 1=t 2=s 3=n
        self._deny_reason_mode: bool = False
        self._approval_queue: asyncio.Queue[str]

        # Approval state
        self._approval_mode: bool = False

        # Spinner state
        self._spinning: bool = False
        self._spin_idx: int = 0
        self._live_text: str = ""
        self._current_state: AgentStepState | None = None
        self._progress_frame_delay: float = _PROGRESS_FRAME_DELAY

        # Session metadata
        self._session_meta: SessionMeta | None = None

        # Input history (non-approval submissions)
        self._input_history: list[str] = []
        self._history_idx: int = -1
        self._history_draft: str = ""

        # Stub retained for live-preview position tracking (_pop_last_output,
        # _remove_preview_output, etc.).  Terminal-replay was removed, so this
        # list is never populated; all slice/len operations on it are no-ops.
        self._exit_log: list[str] = []

        # Ctrl+C state machine
        self._turn_running: bool = False
        self._interrupt_requested: bool = False
        self._interrupt_time: float = 0.0

        # Guards
        self._banner_shown: bool = False
        self._splash_done: bool = False
        self._supports_report_hotkey: bool = False
        self._hero_frame: int = 0
        self._last_report_path: Path | None = None

    # ── Entry point ──────────────────────────────────────────────────────────

    async def run_app(self, runner: "SessionRunner") -> None:
        self._runner = runner
        self._input_queue = asyncio.Queue()
        self._approval_queue = asyncio.Queue()
        self._app = OpenCookApp(self)
        await self._app.run_async()

    # ── Dual-track output ────────────────────────────────────────────────────

    def _write(self, renderable, plain: str | None = None) -> None:
        if self._app:
            self._app.write_log(renderable)

    def _get_prompt_text(self) -> str:
        if self._session_meta and self._session_meta.title:
            return f"  [{self._session_meta.title[:20]}] > "
        return "  > "

    # ── Header + Footer ──────────────────────────────────────────────────────

    # ── Approval preview ─────────────────────────────────────────────────────

    def _render_approval_preview(self, req: ToolApprovalRequest) -> None:
        hdr = Text()
        hdr.append("  ? ", style=f"bold {_AMBER}")
        hdr.append("Approve: ", style=f"bold {_AMBER}")
        hdr.append(req.tool_name, style=f"bold {_CYAN}")
        if req.preview_kind == "command":
            hdr.append("  $ ", style=f"bold {_GREEN}")
            hdr.append(req.preview_text, style=_FG)
            self._write(hdr)
        elif req.preview_kind == "diff":
            self._write(hdr)
            self._write(Syntax(req.preview_text, "diff", theme="monokai"))
        else:
            preview_text = _ellipsize(req.preview_text, 108)
            if preview_text:
                hdr.append("  ", style=_DIM)
                hdr.append(preview_text, style=_DIM if req.preview_kind == "inline" else _FG)
            self._write(hdr)

    # ── Live tool preview (show → replace on complete) ───────────────────────

    def _pop_last_output(self, log_items: int, exit_items: int) -> None:
        """Remove the live preview items so they can be replaced by the final result."""
        total = log_items
        if self._app and total > 0:
            self._app.trim_log(total)
        if exit_items > 0:
            del self._exit_log[-exit_items:]

    def _remove_preview_output(self, cs: ConsoleStep) -> None:
        if cs.preview_log_items > 0:
            if self._app and cs.preview_log_index is not None:
                self._app.query_one("#log", ChatLog).remove_range(cs.preview_log_index, cs.preview_log_items)
            else:
                self._pop_last_output(cs.preview_log_items, 0)
        if cs.preview_exit_items > 0:
            if cs.preview_exit_index is not None:
                start = max(0, min(cs.preview_exit_index, len(self._exit_log)))
                end = min(len(self._exit_log), start + cs.preview_exit_items)
                if start < end:
                    del self._exit_log[start:end]
            else:
                del self._exit_log[-cs.preview_exit_items:]
        cs.preview_log_items = 0
        cs.preview_exit_items = 0
        cs.preview_log_index = None
        cs.preview_exit_index = None

    def _clear_stale_tool_previews(self, current_step_number: int) -> None:
        stale_steps = [
            cs
            for step_no, cs in self.console_step_history.items()
            if step_no != current_step_number and (cs.preview_log_items > 0 or cs.preview_exit_items > 0)
        ]
        stale_steps.sort(
            key=lambda step: step.preview_log_index if step.preview_log_index is not None else -1,
            reverse=True,
        )
        for cs in stale_steps:
            self._remove_preview_output(cs)

    def _print_tool_call_preview(self, agent_step: AgentStep) -> tuple[int, int, int | None, int | None]:
        """Write an 'active / running' tool card for each tool call in this step.

        Returns preview counts plus the insertion indices so the preview can be removed
        even if other log lines are written afterwards.
        """
        log_index = self._app.query_one("#log", ChatLog).item_count() if self._app else None
        exit_index = len(self._exit_log)
        log_items = 0
        exit_items = 0
        for tc in agent_step.tool_calls or []:
            args = tc.arguments or {}
            self._write(Text(""))  # spacer
            log_items += 1
            self._write(
                self._render_tool_call_block(tc.name, args, None, active=True),
                plain=f"{tc.name} {_tool_summary(tc.name, args)}",
            )
            log_items += 1
            exit_items += 1
        return log_items, exit_items, log_index, exit_index

    # ── Step rendering (flowing, no step numbers) ────────────────────────────

    def _print_completed_step(
        self,
        agent_step: AgentStep,
        agent_execution: AgentExecution | None = None,
    ) -> None:
        tool_names = [str(tc.name or "") for tc in (agent_step.tool_calls or [])]
        sequential_only_step = bool(tool_names) and all(
            _is_sequential_thinking_tool(name) for name in tool_names
        )
        """Render a completed step into the log — flowing style, no step headers."""
        # LLM response text (flowing naturally)
        if not sequential_only_step and agent_step.llm_response and agent_step.llm_response.content:
            resp = _truncate_markdown_for_log(agent_step.llm_response.content)
            self._write(Markdown(resp))
            if agent_step.tool_calls:
                continuation = Text()
                continuation.append("    ", style=_BORDER)
                continuation.append(
                    f"... [response continued with {len(agent_step.tool_calls)} tool call(s) below]",
                    style=_DIM,
                )
                self._write(continuation)

        # Tool calls — compact tree style
        for tc in agent_step.tool_calls or []:
            args = tc.arguments or {}
            result_obj = next(
                (tr for tr in (agent_step.tool_results or []) if tr.call_id == tc.call_id),
                None,
            )
            result_chunks: list[str] = []
            if result_obj:
                if result_obj.result and result_obj.result.strip():
                    result_chunks.append(result_obj.result.strip())
                if result_obj.error and result_obj.error.strip():
                    result_chunks.append(result_obj.error.strip())
            result_str = "\n".join(result_chunks) if result_chunks else None
            success = result_obj.success if result_obj else True
            if result_str is None and tc.name in {"bash", "database_execute"} and result_obj is not None:
                result_str = "No output returned."
            self._write(Text(""))  # spacer between tool calls
            block = self._render_tool_call_block(tc.name, args, result_str, success=success)
            self._write(block, plain=f"    {tc.name}  {_tool_summary(tc.name, args)}")

        if agent_step.reflection:
            ref = agent_step.reflection.strip()[:200]
            r = Text()
            r.append("  ~ ", style=f"bold {_MAGENTA}")
            r.append(ref, style=_MAGENTA)
            self._write(r)

        if agent_step.error:
            self._write(
                Text(f"  x {agent_step.error}", style=_RED),
                plain=f"  x {agent_step.error[:70]}",
            )

    # ── CLIConsole abstract method overrides ──────────────────────────────────

    @override
    async def start(self) -> None:
        logger.debug("legacy console method called: start")

    @override
    @override
    def print_task_details(self, details: dict[str, str]) -> None:
        logger.debug("legacy console method called: print_task_details")

    @override
    def write_rich(self, renderable) -> None:
        """Write any Rich renderable directly to the TUI chat log."""
        self._write(renderable)

    def _message_hex_color(self, color: str) -> str:
        color_map = {
            "blue": _CYAN,
            "green": _GREEN,
            "red": _RED,
            "yellow": _AMBER,
            "magenta": _MAGENTA,
        }
        return color_map.get(color, color)

    def _build_message_text(
        self,
        message: str,
        *,
        color: str = "blue",
        bold: bool = False,
        cursor: bool = False,
    ) -> Text:
        hex_color = self._message_hex_color(color)
        text = Text()
        text.append(message, style=f"{'bold ' if bold else ''}{hex_color}")
        if cursor:
            text.append(" |", style=f"bold {_blend_hex(hex_color, _FG, 0.28)}")
        return text

    def _append_glow_text(
        self,
        text: Text,
        content: str,
        *,
        base_color: str,
        tick: int,
        bold: bool = False,
        radius: float = 4.0,
        min_blend: float = 0.10,
        max_blend: float = 0.62,
        phase: int = 0,
    ) -> None:
        sweep = (tick + phase) % max(6, len(content) + 6) if content else 0
        for idx, char in enumerate(content):
            if char == " ":
                text.append(char)
                continue
            distance = abs(sweep - idx)
            glow = max(0.0, 1.0 - distance / max(1.0, radius))
            tone = _blend_hex(base_color, _FG, min_blend + glow * max(0.0, max_blend - min_blend))
            weight = "bold " if bold or glow > 0.4 else ""
            text.append(char, style=f"{weight}{tone}")

    def _append_animated_marker(
        self,
        text: Text,
        *,
        tick: int,
        color: str,
        frames: tuple[str, ...],
        phase: int = 0,
    ) -> None:
        if not frames:
            return
        idx = (tick + phase) % len(frames)
        glyph = frames[idx]
        tone = _blend_hex(color, _FG, 0.28 + (idx / max(1, len(frames) - 1)) * 0.44)
        text.append(glyph, style=f"bold {tone}")
        text.append(" ", style=f"bold {_blend_hex(color, _FG, 0.12)}")

    def _append_progress_prefix_text(
        self,
        text: Text,
        prefix: str,
        *,
        base_color: str,
        tick: int,
        bold: bool = False,
    ) -> None:
        body = prefix
        dots = ""
        if prefix.endswith("..."):
            body = prefix[:-3]
            dots = "..."

        if body:
            first_space = body.find(" ")
            if first_space == -1:
                head, tail = body, ""
            else:
                head, tail = body[:first_space], body[first_space:]

            self._append_glow_text(
                text,
                head,
                base_color=base_color,
                tick=tick,
                bold=bold,
                radius=5.5,
                min_blend=0.08,
                max_blend=0.82,
                phase=2,
            )
            if tail:
                self._append_glow_text(
                    text,
                    tail,
                    base_color=base_color,
                    tick=tick,
                    bold=bold,
                    radius=8.0,
                    min_blend=0.02,
                    max_blend=0.38,
                    phase=6,
                )

        if dots:
            active_dot = tick % len(dots)
            for idx, char in enumerate(dots):
                blend = 0.18 if idx != active_dot else 0.86
                dot_color = _blend_hex(base_color, _FG, blend)
                weight = "bold " if bold or idx == active_dot else ""
                text.append(char, style=f"{weight}{dot_color}")

    def _build_progress_message_text(
        self,
        prefix: str,
        *,
        suffix: str = "",
        color: str = "blue",
        bold: bool = False,
        tick: int = 0,
        cursor: bool = False,
        shimmer_prefix: bool = False,
        marker_frames: tuple[str, ...] | None = None,
    ) -> Text:
        hex_color = self._message_hex_color(color)
        text = Text(no_wrap=True, overflow="crop")
        if marker_frames:
            self._append_animated_marker(text, tick=tick, color=hex_color, frames=marker_frames)
        if shimmer_prefix:
            self._append_progress_prefix_text(
                text,
                prefix,
                base_color=hex_color,
                tick=tick,
                bold=bold,
            )
        else:
            text.append(prefix, style=f"{'bold ' if bold else ''}{hex_color}")
        if suffix:
            text.append("  ", style=_DIM)
            self._append_glow_text(
                text,
                suffix,
                base_color=_blend_hex(_SUBTEXT, hex_color, 0.38),
                tick=tick,
                radius=4.0,
                min_blend=0.10,
                max_blend=0.62,
            )
        if cursor:
            text.append(" |", style=f"bold {_blend_hex(hex_color, _FG, 0.32)}")
        return text

    def print(self, message: str, color: str = "blue", bold: bool = False) -> None:
        self._write(self._build_message_text(message, color=color, bold=bold), plain=message[:80])

    def clear_live_status(self) -> None:
        self._spinning = False
        self._current_state = None
        self._live_text = ""
        if not self._app:
            return
        try:
            self._app.query_one("#status", Static).update("")
            self._app._status_empty = True
        except Exception:
            pass

    async def print_typewriter_async(
        self,
        message: str,
        *,
        color: str = "blue",
        bold: bool = False,
        delay: float = 0.015,
    ) -> None:
        if not message:
            return
        if not self._app:
            self.print(message, color=color, bold=bold)
            return

        if DEMO_RECORDING_MODE:
            # TEMP(demo): keep narration snappy while recording.
            delay = min(delay, DEMO_TYPEWRITER_DELAY)

        rendered_preview = False
        step = 2 if len(message) > 56 else 1
        try:
            for end in range(step, len(message), step):
                if rendered_preview:
                    self._app.trim_log(1)
                self._app.write_log(
                    self._build_message_text(message[:end], color=color, bold=bold, cursor=True)
                )
                rendered_preview = True
                await asyncio.sleep(delay)

            if rendered_preview:
                self._app.trim_log(1)
            self._write(self._build_message_text(message, color=color, bold=bold), plain=message[:80])
        except Exception:
            if rendered_preview and self._app:
                self._app.trim_log(1)
            self.print(message, color=color, bold=bold)

    async def animate_progress_async(
        self,
        prefix: str,
        *,
        phrases: tuple[str, ...],
        color: str = "blue",
        bold: bool = False,
        delay: float = 0.035,
        stop_event: asyncio.Event | None = None,
        final_suffix: str = "complete",
        shimmer_prefix: bool = False,
        marker_frames: tuple[str, ...] | None = None,
    ) -> None:
        if not prefix:
            return
        if not self._app or stop_event is None:
            fallback = f"{prefix}  {final_suffix}".rstrip()
            self.print(fallback, color=color, bold=bold)
            return

        if marker_frames is None and shimmer_prefix:
            marker_frames = _PROGRESS_MARKER_FRAMES

        if DEMO_RECORDING_MODE:
            # TEMP(demo): keep scanning/progress animations aligned with the
            # faster capture pacing without changing the default behavior.
            delay = min(delay, DEMO_PROGRESS_DELAY)

        rendered_preview = False
        tick = 0
        try:
            while not stop_event.is_set():
                animated_suffix, cursor_visible = self._typewriter_frame(
                    tick,
                    phrases,
                    type_chars=_TYPEWRITER_TYPE_CHARS,
                    erase_chars=_TYPEWRITER_ERASE_CHARS,
                    hold_ticks=_TYPEWRITER_HOLD_TICKS,
                    pause_ticks=_TYPEWRITER_PAUSE_TICKS,
                    blink_period=_TYPEWRITER_BLINK_PERIOD,
                )
                if rendered_preview:
                    self._app.trim_log(1)
                self._app.write_log(
                    self._build_progress_message_text(
                        prefix,
                        suffix=animated_suffix,
                        color=color,
                        bold=bold,
                        tick=tick,
                        cursor=cursor_visible,
                        shimmer_prefix=shimmer_prefix,
                        marker_frames=marker_frames,
                    )
                )
                rendered_preview = True
                tick += 1
                await asyncio.sleep(delay)

            if rendered_preview:
                self._app.trim_log(1)
            final_text = self._build_progress_message_text(
                prefix,
                suffix=final_suffix,
                color=color,
                bold=bold,
                tick=tick,
                shimmer_prefix=shimmer_prefix,
                marker_frames=marker_frames,
            )
            self._write(final_text, plain=f"{prefix} {final_suffix}".strip()[:80])
        except Exception:
            if rendered_preview and self._app:
                self._app.trim_log(1)
            fallback = f"{prefix}  {final_suffix}".rstrip()
            self.print(fallback, color=color, bold=bold)

    @override
    def get_task_input(self) -> str | None:
        logger.warning(
            "TextualConsole.get_task_input() called on legacy sync path;"
            " use get_task_input_async() instead"
        )
        return None

    @override
    def get_working_dir_input(self) -> str | None:
        logger.warning(
            "TextualConsole.get_working_dir_input() called on legacy sync path"
        )
        return None

    @override
    def stop(self) -> None:
        logger.debug("legacy console method called: stop")

    # ── Session-level lifecycle ──────────────────────────────────────────────

    @override
    async def session_start(self, session_meta: "SessionMeta") -> None:
        self._session_meta = session_meta
        self._update_header()
        self._update_footer()
        if self._app:
            self._app.query_one(Input).placeholder = self._get_prompt_text()
        if self._banner_shown:
            return
        self._banner_shown = True
        self._animate_splash()

    @override
    async def session_stop(self) -> None:
        self._spinning = False
        if self._app:
            self._app.exit()

    @override
    def session_switch(self, new_session_meta: "SessionMeta") -> None:
        self._session_meta = new_session_meta
        self._update_header()
        self._update_footer()
        if self._app:
            self._app.query_one(Input).placeholder = self._get_prompt_text()
        self._write(
            Text(f"  -- session: {new_session_meta.session_id}", style=_DIM),
            plain=f"[switch] session: {new_session_meta.session_id}",
        )

    @override
    def terminal_clear(self) -> None:
        if self._app:
            self._app.query_one("#log", ChatLog).clear()
        self._exit_log.clear()

    # ── Turn-level lifecycle ─────────────────────────────────────────────────

    @override
    async def begin_turn(self, user_input: str) -> None:
        self.console_step_history = {}
        self.agent_execution = None
        self._current_state = None
        self._live_text = ""
        self._spin_idx = 0
        self._turn_running = True
        self._interrupt_requested = False
        self._interrupt_time = 0.0

        # User message with orange background highlight
        short = user_input[:200]
        self._write(Text(" "))  # spacer
        chip = Text()
        chip.append(f" {short} ", style=f"bold {_SURFACE} on {_ORANGE}")
        self._write(chip, plain=f"\n> {short[:78]}")
        self._write(Text(" "))  # spacer

        self._spinning = True

    @override
    async def end_turn(self, execution: AgentExecution | None) -> None:
        self._turn_running = False
        self.clear_live_status()
        if execution is not None:
            self.agent_execution = execution
            self._print_turn_end_line()
        self._update_footer()

    @override
    async def begin_subagent_run(self) -> None:
        self.console_step_history = {}
        self.agent_execution = None
        self._current_state = None
        self._live_text = ""
        self._spin_idx = 0
        self._turn_running = True
        self._spinning = True

    # ── Async input ──────────────────────────────────────────────────────────

    @override
    async def get_task_input_async(self) -> str | None:
        value = await self._input_queue.get()
        if value is None:
            return None
        stripped = value.strip()
        if stripped.lower() in ("exit", "quit"):
            return None
        return stripped

    # ── Tool approval ────────────────────────────────────────────────────────

    @override
    async def request_tool_approval_async(self, req: ToolApprovalRequest) -> str:
        self._render_approval_preview(req)
        self._selected_approval = 0
        self._deny_reason_mode = False
        self._approval_mode = True
        if self._app:
            self._app.query_one("#log", ChatLog).refresh(layout=True)
            self._app._render_approval_panel()
            approval_input = self._app.query_one(Input)
            approval_input.value = ""
            approval_input.remove_class("deny-mode")
            approval_input.focus()
            self._app.query_one(Input).placeholder = "  ↑↓ select   Enter confirm"
        return await self._approval_queue.get()

    @override
    def update_status(
        self,
        agent_step: AgentStep | None = None,
        agent_execution: AgentExecution | None = None,
    ) -> None:
        if agent_step:
            if agent_step.step_number not in self.console_step_history:
                self.console_step_history[agent_step.step_number] = ConsoleStep(agent_step)

            cs = self.console_step_history[agent_step.step_number]
            cs.agent_step = agent_step
            state = agent_step.state
            self._clear_stale_tool_previews(agent_step.step_number)

            if state != AgentStepState.CALLING_TOOL and (cs.preview_log_items > 0 or cs.preview_exit_items > 0):
                self._remove_preview_output(cs)

            if state in (AgentStepState.THINKING, AgentStepState.REFLECTING):
                self._set_live_status(state, state.value.replace("_", " "))
            elif state == AgentStepState.CALLING_TOOL:
                names = ", ".join(tc.name for tc in (agent_step.tool_calls or []))
                self._set_live_status(state, names or "calling tool")
                if not cs.tool_call_preview_printed:
                    log_items, exit_items, log_index, exit_index = self._print_tool_call_preview(agent_step)
                    cs.preview_log_items = log_items
                    cs.preview_exit_items = exit_items
                    cs.preview_log_index = log_index
                    cs.preview_exit_index = exit_index
                    cs.tool_call_preview_printed = True
            elif state in (AgentStepState.COMPLETED, AgentStepState.ERROR):
                is_task_done_step = any(
                    str(tc.name or "") == "task_done" for tc in (agent_step.tool_calls or [])
                )
                if state == AgentStepState.COMPLETED and is_task_done_step:
                    self.clear_live_status()
                else:
                    self._set_live_status(
                        state,
                        "turn complete" if state == AgentStepState.COMPLETED else "needs attention",
                    )
                if not cs.agent_step_printed:
                    self._print_completed_step(agent_step, agent_execution)
                    cs.agent_step_printed = True

        self.agent_execution = agent_execution

    def _update_header(self) -> None:
        if not self._app:
            return
        header = self._app.query_one("#header", Static)
        width = self._app.size.width or 80

        text = Text()
        _append_inline_brand(text)
        text.append(" v1.0", style=f"bold {_PRIMARY}")

        if self._session_meta and self._session_meta.title and width >= 46:
            text.append(f"  {self._session_meta.title[:28]}", style=_SUBTEXT)

        right_parts: list[str] = []
        if self._session_meta and self._session_meta.cwd and width >= 58:
            right_parts.append(self._session_meta.cwd)
        if self._session_meta and self._session_meta.model and width >= 82:
            right_parts.append(self._session_meta.model)

        if right_parts:
            right = "  |  ".join(right_parts)
            pad_right = max(2, width - len(text.plain) - len(right) - 1)
            text.append(" " * pad_right)
            text.append(right, style=_DIM)

        header.update(text)

    def _update_hero(self, renderable: object) -> None:
        if self._app:
            self._app.query_one("#hero", Static).update(renderable)

    def tick_preview_hero(self) -> None:
        if not self._app or not self._splash_done:
            return
        self._hero_frame += 1
        self._update_hero(self._build_welcome_hero(self._hero_frame))
        if self._spinning:
            self._app.query_one("#log", ChatLog).refresh(layout=True)

    def _kv_table(
        self,
        rows: list[tuple[str, str]],
        *,
        accent: str | None = None,
        frame: int = 0,
    ) -> Table:
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(style=_DIM, width=10, no_wrap=True)
        table.add_column(ratio=1, style=_FG)
        focus_index = (frame // 5) % len(rows) if rows else 0
        for idx, (label, value) in enumerate(rows):
            rendered_label: object = label
            rendered_value: object = value or "-"
            if accent:
                label_color = _blend_hex(_DIM, accent, 0.12 if idx != focus_index else 0.30)
                if idx == focus_index:
                    rendered_label = self._animated_highlight_text(label, label_color, phase=idx * 5)
                    rendered_value = self._animated_highlight_text(
                        value or "-",
                        _blend_hex(_FG, accent, 0.14),
                        phase=idx * 7 + 2,
                        bold=True,
                    )
                else:
                    rendered_label = Text(label, style=label_color)
                    rendered_value = Text(value or "-", style=_FG)
            table.add_row(rendered_label, rendered_value)
        return table

    def _get_complete_trajectory_path(self) -> Path | None:
        if self._last_report_path and self._last_report_path.exists():
            return self._last_report_path
        return None

    @override
    def turn_report_ready(self, report_path: Path) -> None:
        self._last_report_path = report_path
        if self.agent_execution and self.agent_execution.success:
            self._write_complete_trajectory_line(report_path)

    def _public_report_path_label(self, report_path: Path) -> str:
        parts = list(report_path.parts)
        for index, part in enumerate(parts):
            if part.lower() == ".opencook":
                return str(Path(*parts[index:]))
        return report_path.name

    def _build_complete_trajectory_line(self, report_path: Path) -> Text:
        public_label = self._public_report_path_label(report_path)
        line = Text("  Please refer to ", style=_DIM, no_wrap=False, overflow="fold")
        line.append(
            public_label,
            style=f"bold {_PRIMARY} underline link {report_path.as_uri()}",
        )
        hint = " (Ctrl+click or press o to open)" if self._supports_report_hotkey else " (Ctrl+click to open)"
        line.append(" for complete trajectory", style=_DIM)
        line.append(hint, style=_SUBTEXT)
        return line

    def _write_complete_trajectory_line(self, report_path: Path) -> None:
        self._write(Text(" "))
        self._write(
            self._build_complete_trajectory_line(report_path),
            plain=f"trajectory: {self._public_report_path_label(report_path)}",
        )

    def _open_complete_trajectory(self) -> bool:
        report_path = self._get_complete_trajectory_path()
        if not report_path:
            self._write(Text("  No complete trajectory path available.", style=_RED), plain="no trajectory path")
            return False
        try:
            public_label = self._public_report_path_label(report_path)
            if sys.platform == "win32":
                os.startfile(str(report_path))  # type: ignore[attr-defined]
            else:
                opened = webbrowser.open(report_path.as_uri())
                if not opened:
                    raise OSError("browser open returned false")
            self._write(
                Text(f"  Opened trajectory link: {public_label}", style=_DIM),
                plain=f"opened trajectory: {public_label}",
            )
            return True
        except Exception as exc:
            self._write(
                Text(f"  Failed to open trajectory link: {exc}", style=_RED),
                plain=f"open trajectory failed: {exc}",
            )
            return False

    def _update_footer(self) -> None:
        if not self._app:
            return
        footer = self._app.query_one("#footer", Static)
        width = self._app.size.width or 80

        parts: list[tuple[str, str]] = []
        if self._session_meta:
            if self._session_meta.database:
                parts.append((self._session_meta.database, _PRIMARY))
            if width >= 34 and self._session_meta.session_id:
                parts.append((self._session_meta.session_id[:8], _SUBTEXT))

        if width >= 40 and self.agent_execution and self.agent_execution.total_tokens:
            t = self.agent_execution.total_tokens
            parts.append((f"in {t.input_tokens} / out {t.output_tokens}", _DIM))

        if width >= 66:
            parts.append(("Ctrl+C interrupt  o open report  /help", _DIM))

        text = Text(" ")
        for idx, (part_text, part_style) in enumerate(parts):
            if idx:
                text.append("   ", style=_DIM)
            text.append(part_text, style=part_style)
        footer.update(text)

    def _set_live_status(self, state: AgentStepState | None, label: str) -> None:
        normalized = label.replace("_", " ").strip()
        if state != self._current_state or normalized != self._live_text:
            self._spin_idx = 0
        self._current_state = state
        self._live_text = normalized

    def _status_phrase_variants(
        self,
        state: AgentStepState,
        label: str,
        fallback: str,
    ) -> tuple[str, ...]:
        normalized = label.replace("_", " ").strip()
        generic_labels = {
            state.value.replace("_", " "),
            fallback,
            "calling tool",
            "turn complete",
            "needs attention",
        }
        phrases = list(_STATUS_TYPED_PHRASES.get(state, (fallback,)))
        if normalized and normalized.lower() not in {item.lower() for item in generic_labels}:
            phrases.insert(0, normalized)
        return tuple(dict.fromkeys(phrases))

    def _typewriter_frame(
        self,
        tick: int,
        phrases: tuple[str, ...],
        *,
        type_chars: int = 1,
        erase_chars: int = 2,
        hold_ticks: int = 5,
        pause_ticks: int = 2,
        blink_period: int = 6,
    ) -> tuple[str, bool]:
        cleaned = tuple(phrase.strip() for phrase in phrases if phrase and phrase.strip())
        if not cleaned:
            return "", True

        segments: list[tuple[str, int, int, int, int]] = []
        total_ticks = 0
        for phrase in cleaned:
            type_ticks = max(1, (len(phrase) + max(1, type_chars) - 1) // max(1, type_chars))
            erase_ticks = max(1, (len(phrase) + max(1, erase_chars) - 1) // max(1, erase_chars))
            segments.append((phrase, type_ticks, hold_ticks, erase_ticks, pause_ticks))
            total_ticks += type_ticks + hold_ticks + erase_ticks + pause_ticks

        cursor_visible = tick % max(2, blink_period) < max(1, blink_period - 2)
        position = tick % total_ticks
        for phrase, type_ticks, hold_ticks, erase_ticks, pause_ticks in segments:
            if position < type_ticks:
                visible = min(len(phrase), (position + 1) * max(1, type_chars))
                return phrase[:visible], True
            position -= type_ticks
            if position < hold_ticks:
                return phrase, cursor_visible
            position -= hold_ticks
            if position < erase_ticks:
                trimmed = min(len(phrase), (position + 1) * max(1, erase_chars))
                return phrase[: max(0, len(phrase) - trimmed)], True
            position -= erase_ticks
            if position < pause_ticks:
                return "", cursor_visible
            position -= pause_ticks

        return cleaned[-1], cursor_visible

    def _build_scanner_text(self, tick: int) -> Text:
        def _robot_face(stage: str, accent: str, *, phase: int = 0) -> Text:
            eye_fill_symbol, eye_pupil_symbol = _hero_eye_pair(stage, tick, phase=phase)
            face = Text()
            face.append(" [", style=f"bold {accent}")
            face.append(eye_fill_symbol, style=f"bold {accent}")
            face.append(eye_pupil_symbol, style=f"bold {_PUPIL_COLOR}")
            face.append("", style=f"bold {accent}")
            face.append(eye_fill_symbol, style=f"bold {accent}")
            face.append(eye_pupil_symbol, style=f"bold {_PUPIL_COLOR}")
            face.append("]", style=f"bold {accent}")
            return face

        if self._approval_mode:
            text = Text()
            self._append_animated_marker(
                text,
                tick=tick,
                color=_AMBER,
                frames=_STATUS_MARKER_FRAMES,
                phase=1,
            )
            text.append_text(_robot_face("plan", _AMBER, phase=1))
            text.append(" ", style=f"bold {_AMBER}")
            self._append_glow_text(
                text,
                "waiting for approval",
                base_color=_blend_hex(_SUBTEXT, _AMBER, 0.38),
                tick=tick,
                radius=4.0,
                min_blend=0.10,
                max_blend=0.62,
            )
            text.append(" |" if tick % 8 < 6 else "  ", style=f"bold {_AMBER}")
            return text

        _, base_color, fallback = _STATUS_BADGES.get(
            self._current_state or AgentStepState.THINKING,
            ("<._.>", _CYAN, "ready"),
        )
        stage = _STATUS_STAGE_BY_STATE.get(self._current_state or AgentStepState.THINKING, "plan")

        text = Text()
        self._append_animated_marker(
            text,
            tick=tick,
            color=base_color,
            frames=_STATUS_MARKER_FRAMES,
            phase=2,
        )
        label = (self._live_text or fallback).replace("_", " ")
        animated_label, cursor_visible = self._typewriter_frame(
            tick,
            self._status_phrase_variants(self._current_state or AgentStepState.THINKING, label, fallback),
            type_chars=_TYPEWRITER_TYPE_CHARS,
            erase_chars=_TYPEWRITER_ERASE_CHARS,
            hold_ticks=_TYPEWRITER_HOLD_TICKS,
            pause_ticks=_TYPEWRITER_PAUSE_TICKS,
            blink_period=_TYPEWRITER_BLINK_PERIOD,
        )
        text.append_text(_robot_face(stage, base_color))
        text.append(" ", style=f"bold {base_color}")
        self._append_glow_text(
            text,
            animated_label or fallback,
            base_color=_blend_hex(_SUBTEXT, base_color, 0.38),
            tick=tick,
            radius=4.0,
            min_blend=0.10,
            max_blend=0.62,
        )
        text.append(" |" if cursor_visible else "  ", style=f"bold {base_color}")
        return text

    def _animate_splash(self) -> None:
        if not self._app or self._splash_done:
            return
        self._splash_done = True
        self._hero_frame = 0
        self._update_hero(self._build_welcome_hero(self._hero_frame))

    def _positioned_line(
        self,
        width: int,
        items: list[tuple[int, str, str]],
    ) -> Text:
        line = Text(no_wrap=True, overflow="crop")
        cursor = 0
        for x, content, style in sorted(items, key=lambda item: item[0]):
            x = max(0, min(x, max(0, width - len(content))))
            if x > cursor:
                line.append(" " * (x - cursor))
            line.append(content, style=style)
            cursor = x + len(content)
        if cursor < width:
            line.append(" " * (width - cursor))
        return line

    def _twinkle_star(self, frame: int, phase: int) -> tuple[str, str]:
        states = [
            (" ", _DIM),
            (".", _DIM),
            ("+", _SUBTEXT),
            ("*", _FG),
            ("*", _PRIMARY),
            ("+", _ORANGE),
        ]
        return states[(frame + phase) % len(states)]

    def _build_starfield_lines(self, frame: int) -> list[Text]:
        width = max(28, min(100, (self._app.size.width if self._app else 100) - 8))
        star_specs = [
            (0, 0.04, 0),
            (0, 0.31, 1),
            (0, 0.72, 2),
            (1, 0.10, 3),
            (1, 0.44, 4),
            (1, 0.85, 5),
            (2, 0.18, 6),
            (2, 0.58, 7),
            (3, 0.27, 8),
            (3, 0.76, 9),
        ]
        keyword_specs: list[tuple[int, float, str]] = []
        if width >= 84:
            keyword_specs = [
                # (0, 0.18, "SELECT"),
                # (1, 0.34, "JOIN"),
                # (2, 0.50, "WHERE"),
                # (1, 0.77, "INDEX"),
                (0, 0.14, "ADD FUNCTION"),
                (1, 0.34, "CHANGE LAYOUT"),
                (2, 0.54, "BUILD API"),
                (1, 0.77, "FIX BUG"),
            ]
        elif width >= 62:
            keyword_specs = [
                # (0, 0.16, "SELECT"),
                # (1, 0.46, "JOIN"),
                # (2, 0.72, "WHERE"),
                (0, 0.12, "ADD FUNCTION"),
                (1, 0.46, "CHANGE LAYOUT"),
                (2, 0.75, "BUILD API"),
            ]

        line_count = 4 if width >= 72 else 3 if width >= 46 else 2
        rows: list[list[tuple[int, str, str]]] = [[] for _ in range(line_count)]
        for row, frac, phase in star_specs:
            if row >= line_count:
                continue
            char, style = self._twinkle_star(frame, phase)
            rows[row].append((int((width - 1) * frac), char, style))
        for row, frac, word in keyword_specs:
            if row >= line_count:
                continue
            rows[row].append((int((width - len(word)) * frac), word, _DIM))
        return [self._positioned_line(width, row) for row in rows]

    def _build_stage_chips(self, active_index: int, *, frame: int = 0) -> Text:
        specs = [
            ("Plan", _CYAN),
            ("Code", _GREEN),
            ("Test", _RED),
            ("Done", _AMBER),
        ]
        chips = Text(no_wrap=True, overflow="crop")
        chips.append("  ")
        for index, (title, color) in enumerate(specs):
            if index:
                chips.append("   ", style=_DIM)
            if index == active_index:
                chips.append_text(self._animated_highlight_text(title, color, phase=frame + index * 4, bold=True))
            else:
                chips.append(title, style=_SUBTEXT)
        return chips

    def _hero_active_stage_index(self, frame: int, stage_count: int) -> int:
        stage_map = {
            AgentStepState.THINKING: 0,
            AgentStepState.CALLING_TOOL: 1,
            AgentStepState.REFLECTING: 2,
            AgentStepState.COMPLETED: 3,
            AgentStepState.ERROR: 2,
        }
        if self._turn_running or self._spinning or self._approval_mode:
            return stage_map.get(self._current_state or AgentStepState.THINKING, 0)
        if stage_count <= 0:
            return 0
        return (frame // 3) % stage_count

    def _build_brand_wordmark(
        self,
        width: int,
        *,
        active_title: str,
        active_color: str,
        frame: int,
    ) -> Group:
        inner_width = max(28, width - 2)
        eyebrow = Text(
            "PROJECT-PERSONALIZATION · FEATURE-DRIVEN · MULTI-AGENT",
            style=f"bold {_SUBTEXT}",
            no_wrap=True,
            overflow="crop",
        )

        if _ACTIVE_BRAND_KEY != "dbcooker":
            return _build_alternate_brand_wordmark(
                inner_width,
                frame=frame,
                eyebrow=eyebrow,
                active_title=active_title,
                active_color=active_color,
            )

        db_glyphs = {
            "D": [
                "█████████   ",
                "██     ███  ",
                "██      ██  ",
                "██      ██  ",
                "██      ██  ",
                "██     ███  ",
                "█████████   ",
            ],
            "B": [
                "████████   ",
                "██     ██  ",
                "████████   ",
                "██     ██  ",
                "██      ██ ",
                "██     ██  ",
                "████████   ",
            ],
        }
        c_glyph = [
            " ████████  ",
            "██      ██ ",
            "██         ",
            "██         ",
            "██         ",
            "██      ██ ",
            " ████████  ",
        ]
        lower_glyphs = {
            "o": [
                " ▄▄▄▄▄  ",
                "█     █ ",
                "█     █ ",
                " ▀▀▀▀▀  ",
            ],
            "k": [
                "█   ██  ",
                "█ ██    ",
                "███     ",
                "█  █▄▄  ",
            ],
            "e": [
                " ██████  ",
                "██▄▄▄▄▄█  ",
                "██        ",
                " █▄▄▄▄▄█  ",
            ],
            "r": [
                "██████  ",
                "██  ██  ",
                "██      ",
                "██      ",
            ],
        }
        lower_specs = [
            ("o", _blend_hex(_BLUE, _PURPLE, 0.76)),
            ("o", _blend_hex(_BLUE, _PURPLE, 0.84)),
            ("k", _blend_hex(_PURPLE, "#8B5CF6", 0.35)),
            ("e", _blend_hex(_PURPLE, "#8B5CF6", 0.62)),
            ("r", _blend_hex(_PURPLE, "#A855F7", 0.78)),
        ]

        if inner_width < 58:
            compact = Text(no_wrap=True, overflow="crop")
            compact.append(_brand_lead_text(), style=f"bold {_ACTIVE_BRAND['lead_color']}")
            compact.append(_brand_accent_text(), style=f"bold {_blend_hex(_BLUE, _PURPLE, 0.58)}")
            return Group(compact, eyebrow)

        def _fixed_row(text: Text, row_width: int) -> Text:
            line = Text(no_wrap=True, overflow="crop")
            line.append_text(text)
            pad = max(0, row_width - len(text.plain))
            if pad:
                line.append(" " * pad)
            return line

        def _blank_row(row_width: int) -> Text:
            return Text(" " * row_width, no_wrap=True, overflow="crop")

        def _solid_cells(content: str, style: str | None) -> list[tuple[str, str | None]]:
            return [(char, style if char != " " else None) for char in content]

        def _cells_to_text(cells: list[tuple[str, str | None]]) -> Text:
            line = Text(no_wrap=True, overflow="crop")
            if not cells:
                return line
            current_style = cells[0][1]
            buffer = ""
            for char, style in cells:
                if style == current_style:
                    buffer += char
                else:
                    line.append(buffer, style=current_style)
                    buffer = char
                    current_style = style
            if buffer:
                line.append(buffer, style=current_style)
            return line

        outline = active_color
        hat_top = _ORANGE
        hat_brim = _ORANGE_DARK
        foot_fill = "#8b5a2b"
        fill_shades = {
            "plan": ("#16384d", "#21506d"),
            "code": ("#173f2b", "#22603f"),
            "test": ("#472634", "#63374b"),
            "done": ("#4d3a16", "#6a531f"),
        }
        fill_light, fill_dark = fill_shades.get(active_title.lower(), ("#16384d", "#21506d"))
        mouth_symbol = {
            "plan": "o",
            "code": "=",
            "test": "~",
            "done": "w",
        }.get(active_title.lower(), ".")
        pupil_color = "#020617"

        def _animated_block_lens(phase: int = 0) -> tuple[str, str]:
            states = [("Tp", "qq"), ("pT", "qq")]
            top, bottom = states[((frame // 2) + phase) % len(states)]
            return top, bottom

        def _animated_eye_shape(phase: int = 0) -> tuple[str, str]:
            states_by_stage = {
                "plan": [("✦", "▶"), ("▶", "✦")],
                "code": [("■", "✦"), ("✦", "■")],
                "test": [("▶", "▶"), ("◀", "◀")],
                "done": [("■", "◀"), ("◀", "■")],
            }
            states = states_by_stage.get(active_title.lower(), [("✦", "▶"), ("▶", "✦")])
            return states[((frame // 2) + phase) % len(states)]

        def _animated_blush(phase: int = 0) -> tuple[str, str]:
            states = [
                ("━", "#d9778b"),
                ("━", "#f472b6"),
                ("━", "#fb7185"),
                ("━", "#f472b6"),
            ]
            return states[((frame // 2) + phase) % len(states)]

        def _animated_mouth(phase: int = 0) -> str:
            states_by_stage = {
                "plan": [".", "·", ".", "·"],
                "code": ["=", "≡", "=", "≡"],
                "test": ["~", "≈", "~", "≈"],
                "done": ["w", "-", "w", "-"],
            }
            states = states_by_stage.get(active_title.lower(), [mouth_symbol] * 4)
            return states[((frame // 2) + phase) % len(states)]

        def _animated_cheek_row(phase: int = 0) -> str:
            states = [
                "   │ R M R  │ ",
                "   │  R M R │ ",
            ]
            return states[((frame // 2) + phase) % len(states)]

        def _mascot_body_cells(pattern: str) -> list[tuple[str, str | None]]:
            cells: list[tuple[str, str | None]] = []
            blush_char, blush_color = _animated_blush()
            animated_mouth = _animated_mouth()
            eye_fill_symbol, eye_pupil_symbol = _animated_eye_shape()
            for char in pattern:
                if char in {"E", "e", "T", "B"}:
                    cells.append((eye_fill_symbol, f"bold {outline}"))
                elif char == "M":
                    cells.append((animated_mouth, f"bold {outline}"))
                elif char in {"p", "q"}:
                    cells.append((eye_pupil_symbol, f"bold {pupil_color}"))
                elif char == "R":
                    cells.append((blush_char, f"bold {blush_color}"))
                elif char == "F":
                    cells.append(("■", f"bold {outline}"))
                elif char == "█":
                    cells.append(("█", f"bold {foot_fill}"))
                elif char in {"╱", "╲", "│", "╰", "╯", "─", "┌", "┐", "└", "┘"}:
                    cells.append((char, outline))
                else:
                    cells.append((char, None))
            return cells

        db_width = len(db_glyphs["D"][0]) + 1 + len(db_glyphs["B"][0])
        db_lines: list[Text] = []
        for row in range(7):
            line = Text(no_wrap=True, overflow="crop")
            line.append_text(
                _animated_gradient_text(
                    db_glyphs["D"][row],
                    frame=frame,
                    start_t=0.00,
                    end_t=0.06,
                    phase=row,
                )
            )
            line.append(" ")
            line.append_text(
                _animated_gradient_text(
                    db_glyphs["B"][row],
                    frame=frame,
                    start_t=0.04,
                    end_t=0.10,
                    phase=row + 4,
                )
            )
            db_lines.append(_fixed_row(line, db_width))

        c_width = len(c_glyph[0])
        c_lines: list[Text] = []
        for row in range(7):
            line = Text(no_wrap=True, overflow="crop")
            line.append_text(
                _animated_gradient_text(
                    c_glyph[row],
                    frame=frame,
                    start_t=0.62,
                    end_t=0.76,
                    phase=row + 8,
                )
            )
            c_lines.append(_fixed_row(line, c_width))

        lower_word_width = sum(len(lower_glyphs[ch][0]) for ch, _ in lower_specs) + (len(lower_specs) - 1)
        lens_top, _ = _animated_block_lens()
        mascot_patterns = [
            _solid_cells("     ▄▄▄▄▄▄     ", hat_top),
            _solid_cells("   ██████████   ", hat_brim),
            _mascot_body_cells(f"   ╱  {lens_top}{lens_top}  ╲ "),
            _mascot_body_cells(_animated_cheek_row()),
            _mascot_body_cells("   ╰─██──██─╯   "),
        ]
        mascot_width = max(len(row) for row in mascot_patterns)
        oo_width = len(lower_glyphs["o"][0]) * 2 + 1
        mascot_left_pad = max(0, (oo_width - mascot_width) // 2)

        spoon_style = f"bold {_blend_hex(_ORANGE, _FG, 0.45)}"
        spoon_rows = [
            _solid_cells("◯ ", spoon_style),
            _solid_cells(" ╲", spoon_style),
        ]
        spoon_x = max(0, mascot_left_pad - 1)

        pot_style = "bold #8fb6d9"
        steam_style = "bold #d7e7f5"
        pot_fill_style = f"bold {_blend_hex(_AMBER, _FG, 0.12)}"
        steam_frames = [
            "  ˚  ˚  ",
            " ˚  ˚   ",
            "   ˚  ˚ ",
            "  ~  ~  ",
        ]
        pot_fill_frames = [
            "▒░▒▓",
            "░▒▓▒",
            "▒▓▒░",
            "▓▒░▒",
        ]
        steam_row = _solid_cells(steam_frames[(frame // 2) % len(steam_frames)], steam_style)
        pot_fill_row = (
            _solid_cells("□┤", pot_style)
            + _solid_cells(pot_fill_frames[(frame // 2) % len(pot_fill_frames)], pot_fill_style)
            + _solid_cells("├□", pot_style)
        )
        pot_rows = [
            steam_row,
            _solid_cells("  ▁▄▄▁  ", pot_style),
            pot_fill_row,
            _solid_cells(" ╰─▄▄─╯ ", pot_style),
        ]
        pot_width = max(len(row) for row in pot_rows)
        pot_x = mascot_left_pad + mascot_width - 2
        third_width = max(lower_word_width, mascot_left_pad + mascot_width, pot_x + pot_width)
        lower_start = len(mascot_patterns) - 1
        third_height = lower_start + len(lower_glyphs["o"])

        lower_rows: list[list[tuple[str, str | None]]] = []
        for row in range(4):
            row_cells: list[tuple[str, str | None]] = []
            for index, (char, color) in enumerate(lower_specs):
                glow = ((frame + index * 2 + row) % 8) / 7
                animated_color = _blend_hex(color, _FG, 0.06 + glow * 0.16)
                row_cells.extend(_solid_cells(lower_glyphs[char][row], f"bold {animated_color}"))
                if index < len(lower_specs) - 1:
                    row_cells.append((" ", None))
            lower_rows.append(row_cells)

        canvas_chars = [[" " for _ in range(third_width)] for _ in range(third_height)]
        canvas_styles: list[list[str | None]] = [[None for _ in range(third_width)] for _ in range(third_height)]

        def _place_row(y: int, x: int, cells: list[tuple[str, str | None]]) -> None:
            if y < 0 or y >= third_height:
                return
            for offset, (char, style) in enumerate(cells):
                px = x + offset
                if px < 0 or px >= third_width or char == " ":
                    continue
                canvas_chars[y][px] = char
                canvas_styles[y][px] = style

        for row_index, cells in enumerate(lower_rows):
            _place_row(lower_start + row_index, 0, cells)
        for row_index, cells in enumerate(mascot_patterns):
            _place_row(row_index, mascot_left_pad, cells)
        for row_index, cells in enumerate(spoon_rows):
            _place_row(1 + row_index, spoon_x, cells)
        for row_index, cells in enumerate(pot_rows):
            _place_row(1 + row_index, pot_x, cells)

        third_lines: list[Text] = []
        for row_index in range(third_height):
            row_cells = list(zip(canvas_chars[row_index], canvas_styles[row_index]))
            third_lines.append(_cells_to_text(row_cells))

        total_rows = third_height
        c_offset = 1
        blank_db = _blank_row(db_width)
        blank_c = _blank_row(c_width)
        blank_third = _blank_row(third_width)
        brand_rows: list[Text] = []
        for row in range(total_rows):
            line = Text(no_wrap=True, overflow="crop")
            line.append_text(db_lines[row] if row < len(db_lines) else blank_db)
            line.append(" ")
            c_index = row - c_offset
            line.append_text(c_lines[c_index] if 0 <= c_index < len(c_lines) else blank_c)
            line.append(" ")
            line.append_text(third_lines[row] if row < len(third_lines) else blank_third)
            brand_rows.append(line)

        subtitle = Text()
        subtitle.append(_brand_lead_text(), style=f"bold {_ACTIVE_BRAND['lead_color']}")
        subtitle.append(_brand_accent_text(), style=f"bold {_blend_hex(_PURPLE, '#A855F7', 0.35)}")
        return Group(*brand_rows, subtitle, eyebrow)

    def _build_welcome_hero(self, frame: int) -> Panel:
        active_specs = [
            ("Plan", _CYAN),
            ("Code", _GREEN),
            ("Test", _RED),
            ("Done", _AMBER),
        ]
        active_index = self._hero_active_stage_index(frame, len(active_specs))
        active_title, active_color = active_specs[active_index]

        width = self._app.size.width if self._app else 120
        compact = width < 34
        meta_rows = [
            ("session", self._session_meta.session_id[:8] if self._session_meta else "session"),
            (
                "model",
                (self._session_meta.model if self._session_meta else "default")
                if not compact
                else (self._session_meta.model[:18] if self._session_meta and self._session_meta.model else "default"),
            ),
            ("cwd", self._session_meta.cwd if self._session_meta else PROJECT_ROOT.name),
            ("database", self._session_meta.database if self._session_meta else "sqlite"),
        ]
        brand_width = max(38, (width * 2) // 3 - 4 if width >= 108 else width - 8)
        left = Group(
            self._build_brand_wordmark(
                brand_width,
                active_title=active_title,
                active_color=active_color,
                frame=frame,
            ),
        )

        stage_line = Text("  Active stage: ", style=f"bold {active_color}")
        stage_line.append_text(
            self._animated_highlight_text(active_title, active_color, phase=frame + 3, bold=True)
        )

        live_session_title = self._animated_highlight_text(
            "  Live Session",
            _PRIMARY,
            phase=frame + 1,
            bold=True,
        )
        right = Group(
            live_session_title,
            stage_line,
            self._kv_table(meta_rows, accent=active_color, frame=frame),
            Text(" "),
            self._build_stage_chips(active_index, frame=frame),
        )

        if width >= 108:
            layout = Table.grid(expand=True, padding=(0, 2))
            layout.add_column(ratio=6)
            layout.add_column(ratio=3)
            layout.add_row(left, right)
            body: object = layout
        else:
            body = Group(left, Text(" "), live_session_title)

        if width >= 90:
            subtitle = Text(" Ctrl+C interrupt  .  o open report  .  /help commands ", style=_DIM)
        elif width >= 66:
            subtitle = Text(" Ctrl+C  .  o open report  .  /help ", style=_DIM)
        elif width >= 30:
            subtitle = Text(" Ctrl+C  .  o  .  /help ", style=_DIM)
        else:
            subtitle = Text(" Ctrl+C . o ", style=_DIM)

        hero_title = _append_centered_brand_title(Text(no_wrap=True, overflow="crop"), width)

        content = Group(
            hero_title,
            *self._build_starfield_lines(frame),
            body,
        )
        return Panel(
            content,
            subtitle=subtitle,
            border_style=_BORDER,
            box=box.ASCII,
            padding=(0, 1),
            expand=True,
        )

    def _tool_animation_phase(self, tool_name: str, arguments: dict) -> int:
        seed = sum(ord(ch) for ch in tool_name)
        seed += sum(ord(ch) for ch in json.dumps(arguments, sort_keys=True, ensure_ascii=False))
        return seed % 13

    def _animated_typed_text(
        self,
        text_value: str,
        color: str,
        *,
        phase: int = 0,
        bold: bool = False,
        reserve_width: int | None = None,
    ) -> Text:
        typed = Text()
        if not text_value:
            return typed
        speed = 8
        cycle = len(text_value) + 14
        progress = (self._hero_frame * speed + phase) % cycle
        visible = min(len(text_value), progress)
        weight = "bold " if bold else ""
        cursor_style = f"bold {_blend_hex(color, _FG, 0.26)}"
        if visible:
            typed.append(text_value[:visible], style=f"{weight}{color}")
        if visible < len(text_value):
            typed.append("▋" if (self._hero_frame + phase) % 2 == 0 else "▌", style=cursor_style)
        elif (self._hero_frame + phase) % 4 < 2:
            typed.append(" ", style=f"{weight}{color}")
            typed.append("▋", style=cursor_style)
        if reserve_width is not None:
            missing = max(0, reserve_width - len(typed.plain))
            if missing:
                typed.append(" " * missing, style=f"{weight}{color}")
        return typed

    def _animated_highlight_text(
        self,
        text_value: str,
        color: str,
        *,
        phase: int = 0,
        bold: bool = False,
    ) -> Text:
        highlighted = Text()
        if not text_value:
            return highlighted
        weight = "bold " if bold else ""
        band = 6
        speed = 5
        travel = len(text_value) + band * 2
        glow_center = (self._hero_frame * speed + phase) % travel - band
        for idx, char in enumerate(text_value):
            distance = abs(idx - glow_center)
            if distance <= band:
                t = 1.0 - (distance / (band + 1))
                glow = _blend_hex(color, _FG, 0.12 + t * 0.48)
                style = f"{weight}{glow}"
            else:
                style = f"{weight}{color}"
            highlighted.append(char, style=style)
        return highlighted

    def _tool_activity_line(
        self,
        tool_name: str,
        arguments: dict,
        result: str | None,
        *,
        active: bool,
    ) -> str:
        if tool_name == "task_done":
            if active:
                return "Wrapping up this turn"
            return result or "What would you like me to do next?"
        if _is_sequential_thinking_tool(tool_name):
            thought_number = arguments.get("thought_number")
            total_thoughts = arguments.get("total_thoughts")
            if isinstance(thought_number, int) and isinstance(total_thoughts, int):
                label = f"Thought {thought_number}/{max(thought_number, total_thoughts)}"
            else:
                label = "Sequential thinking"
            if active:
                return label
            next_needed = arguments.get("next_thought_needed")
            if isinstance(next_needed, bool):
                return f"{label} {'continuing' if next_needed else 'complete'}"
            return label
        if tool_name == "bash":
            command = str(arguments.get("command", ""))
            if command.startswith("rg "):
                return "Searched for 1 pattern, read 5 files"
            if command.startswith("pytest "):
                return f"Ran {command}"
            return f"Ran {command or 'shell command'}"
        if tool_name == "database_execute":
            return "Executed SQL query plan check"
        if tool_name == "str_replace_based_edit_tool":
            path = str(arguments.get("path", "file"))
            command = str(arguments.get("command", "") or "")
            if active:
                if command == "view":
                    return f"Inspecting {path}"
                if command == "insert":
                    return f"Inserting text into {path}"
                if command == "str_replace":
                    return f"Applying text replacement in {path}"
                if command == "create":
                    return f"Creating {path}"
                return f"Preparing edit for {path}"
            if command == "view":
                view_range = arguments.get("view_range")
                if isinstance(view_range, list) and len(view_range) == 2:
                    end = "end" if view_range[1] == -1 else str(view_range[1])
                    return f"Viewed {path} lines {view_range[0]}-{end}"
                return f"Viewed {path}"
            if command == "insert":
                insert_line = arguments.get("insert_line")
                if isinstance(insert_line, int):
                    return f"Inserted text into {path} after line {insert_line}"
                return f"Inserted text into {path}"
            if command == "str_replace":
                if result and "No replacement was performed" in result:
                    return f"Text replacement failed in {path}"
                return f"Applied text replacement in {path}"
            if command == "create":
                return f"Created {path}"
            return f"Updated {path}"
        if tool_name == "json_edit_tool":
            target = arguments.get("json_path", "JSON file")
            return f"Updated {target}" if not active else f"Preparing JSON update for {target}"
        if "subagent" in tool_name:
            return _ellipsize(str(arguments.get("task", "Consulted helper agent")), 90)
        return _tool_summary(tool_name, arguments)

    @staticmethod
    def _extract_embedded_json_payload(content: str | None) -> dict[str, object] | None:
        if not content:
            return None
        candidates: list[str] = []
        for marker in ("Status:\n", "Details:\n"):
            if marker in content:
                candidates.append(content.split(marker, 1)[1].strip())
        candidates.append(content.strip())
        for candidate in candidates:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start < 0 or end < start:
                continue
            try:
                payload = json.loads(candidate[start:end + 1])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def _sequential_thinking_payload(
        self,
        arguments: dict,
        result: str | None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key in (
            "thought",
            "thought_number",
            "total_thoughts",
            "next_thought_needed",
            "branch_from_thought",
            "branch_id",
            "is_revision",
            "revises_thought",
            "needs_more_thoughts",
        ):
            value = arguments.get(key)
            if value is not None:
                payload[key] = value
        parsed = self._extract_embedded_json_payload(result)
        if parsed:
            payload.update(parsed)
        return payload

    def _append_sequential_thinking_result(
        self,
        items: list[object],
        arguments: dict,
        result: str | None,
        *,
        success: bool,
    ) -> bool:
        payload = self._sequential_thinking_payload(arguments, result)
        thought = str(payload.get("thought", "") or "").strip()
        rows: list[tuple[str, str]] = []

        thought_number = payload.get("thought_number")
        total_thoughts = payload.get("total_thoughts")
        if isinstance(thought_number, int) and isinstance(total_thoughts, int):
            rows.append(("step", f"{thought_number}/{max(thought_number, total_thoughts)}"))

        next_needed = payload.get("next_thought_needed")
        if isinstance(next_needed, bool):
            rows.append(("next", "continue" if next_needed else "complete"))

        if not thought and not rows:
            return False

        items.append(Text(""))

        if thought:
            thought_line = Text()
            thought_line.append("    thought", style=_DIM)
            thought_line.append("  ", style=_DIM)
            thought_line.append(_ellipsize(thought, 88), style=_FG if success else _RED)
            items.append(thought_line)

        if rows:
            table = Table.grid(expand=False, padding=(0, 1))
            table.add_column(style=_DIM, width=9, no_wrap=True)
            table.add_column(style=_FG if success else _RED)
            for label, value in rows:
                table.add_row(label, value)
            items.append(table)

        return True

    def _build_tool_call_block(
        self,
        tool_name: str,
        arguments: dict,
        result: str | None,
        *,
        success: bool = True,
        active: bool = False,
    ):
        return self._build_tool_call_block_stable(
            tool_name,
            arguments,
            result,
            success=success,
            active=active,
        )

    def _build_tool_call_block_stable(
        self,
        tool_name: str,
        arguments: dict,
        result: str | None,
        *,
        success: bool = True,
        active: bool = False,
    ):
        return self._build_tool_call_block_fixed_layout(
            tool_name,
            arguments,
            result,
            success=success,
            active=active,
        )
        accent = _tool_accent(tool_name)
        mode = _tool_icon_mode(tool_name)[1]
        awaiting_approval = active and self._approval_mode
        items: list[object] = []

        if tool_name == "task_done":
            prompt = _ellipsize(
                (result or "What would you like me to do next?").replace("\r", " ").replace("\n", " ").strip(),
                110,
            )
            line = Text()
            line.append("  ")
            badge_fg = "#9ca3af" if success else "#a1a1aa"
            line.append("•", style=f"bold {badge_fg}")
            line.append(" ")
            if active:
                line.append_text(self._animated_highlight_text("Wrapping up this turn", accent, phase=3, bold=True))
                line.append("  ")
                status_text = "(awaiting approval)" if awaiting_approval else "(running...)"
                line.append_text(
                    self._animated_typed_text(
                        status_text,
                        _AMBER,
                        phase=8,
                        bold=True,
                        reserve_width=len(status_text) + 2,
                    )
                )
            else:
                line.append(prompt, style=f"bold {accent}")
            return line

        status_text = "(awaiting approval)" if awaiting_approval else "(running...)"
        summary = _ellipsize(
            self._tool_activity_line(tool_name, arguments, result, active=active),
            68 if active and awaiting_approval else 78 if active else 96,
        )
        detail = _tool_summary(tool_name, arguments)
        phase = self._tool_animation_phase(tool_name, arguments)

        header = Text()
        header.append("  ")
        badge_fg = "#9ca3af" if success else "#a1a1aa"
        header.append("●", style=f"bold {badge_fg}")
        header.append(" ")
        if active:
            header.append_text(self._animated_highlight_text(summary, accent, phase=phase, bold=True))
        else:
            header.append(summary, style=f"bold {accent}")
        if active:
            header.append("  ")
            header.append_text(
                self._animated_typed_text(
                    status_text,
                    _AMBER,
                    phase=phase + 5,
                    bold=True,
                    reserve_width=len(status_text) + 2,
                )
            )
        items.append(header)

        meta = Text()
        meta.append("    ", style=_BORDER)
        meta.append(tool_name, style=f"bold {accent}" if active else accent)
        if detail and detail != summary:
            meta.append("  ", style=_DIM)
            meta.append(detail, style=_DIM)
        items.append(meta)

        if mode == "diff" and arguments.get("command") == "str_replace":
            old_lines = (arguments.get("old_str", "") or "").splitlines()
            new_lines = (arguments.get("new_str", "") or "").splitlines()
            diff = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=str(arguments.get("path", "before")),
                    tofile=str(arguments.get("path", "after")),
                    lineterm="",
                )
            )
            items.append(Text(""))
            for dl in diff[:16]:
                line = Text(no_wrap=True, overflow="crop")
                padded = f" {dl}".ljust(200)
                if dl.startswith("+") and not dl.startswith("+++"):
                    line.append(padded, style=f"bold {_GREEN} on #102617")
                elif dl.startswith("-") and not dl.startswith("---"):
                    line.append(padded, style=f"bold {_RED} on #2a1014")
                elif dl.startswith("@@"):
                    line.append(padded, style=f"bold {_MAGENTA} on #0f1830")
                else:
                    line.append(padded, style=f"{_SUBTEXT} on {_tool_accent_bg(tool_name)}")
                items.append(line)
            if len(diff) > 16:
                items.append(Text(f"    ... ({len(diff) - 16} more lines)", style=_BORDER))

        handled_structured_result = False
        if result and _is_sequential_thinking_tool(tool_name):
            handled_structured_result = self._append_sequential_thinking_result(
                items,
                arguments,
                result,
                success=success,
            )

        if result and (mode != "diff" or arguments.get("command") != "str_replace") and not handled_structured_result:
            out_lines = result.strip().splitlines()
            shown = out_lines[:8]
            result_style = _SUBTEXT if success else _RED
            items.append(Text(""))
            for ol in shown:
                line = Text()
                line.append("    ", style=_BORDER)
                line.append(ol[:120], style=result_style)
                items.append(line)
            if len(out_lines) > 8:
                items.append(Text(f"    ... ({len(out_lines) - 8} more lines)", style=_BORDER))

        return Group(*items)

    def _build_tool_call_block_fixed_layout(
        self,
        tool_name: str,
        arguments: dict,
        result: str | None,
        *,
        success: bool = True,
        active: bool = False,
    ):
        accent = _tool_accent(tool_name)
        mode = _tool_icon_mode(tool_name)[1]
        awaiting_approval = active and self._approval_mode
        items: list[object] = []
        width = self._app.size.width if self._app else 100

        def build_prefix() -> Text:
            badge_fg = "#9ca3af" if success else "#a1a1aa"
            prefix = Text(no_wrap=True, overflow="crop")
            prefix.append("  ")
            prefix.append("•", style=f"bold {badge_fg}")
            prefix.append(" ")
            return prefix

        def build_header(summary_text: str, *, phase: int) -> Table:
            status_text = "(awaiting approval)" if awaiting_approval else "(running...)"
            status_width = len(status_text) + 2 if active else 0
            header = Table.grid(expand=True, padding=(0, 0))
            header.add_column(width=4, no_wrap=True)
            header.add_column(ratio=1, no_wrap=True)
            if active:
                header.add_column(width=status_width, no_wrap=True)

            if active:
                summary_renderable = self._animated_highlight_text(
                    summary_text,
                    accent,
                    phase=phase,
                    bold=True,
                )
                status_renderable = self._animated_typed_text(
                    status_text,
                    _AMBER,
                    phase=phase + 5,
                    bold=True,
                    reserve_width=status_width,
                )
                header.add_row(build_prefix(), summary_renderable, status_renderable)
            else:
                summary_renderable = Text(no_wrap=True, overflow="crop")
                summary_renderable.append(summary_text, style=f"bold {accent}")
                header.add_row(build_prefix(), summary_renderable)
            return header

        if tool_name == "task_done":
            prompt = _ellipsize(
                (result or "What would you like me to do next?").replace("\r", " ").replace("\n", " ").strip(),
                110,
            )
            if active:
                return build_header(
                    _ellipsize("Wrapping up this turn", max(18, width - len("(awaiting approval)") - 12)),
                    phase=3,
                )
            line = Text(no_wrap=True, overflow="crop")
            line.append_text(build_prefix())
            line.append(prompt, style=f"bold {accent}")
            return line

        status_text = "(awaiting approval)" if awaiting_approval else "(running...)"
        summary_cap = 68 if active and awaiting_approval else 78 if active else 96
        status_width = len(status_text) + 2 if active else 0
        summary = _ellipsize(
            self._tool_activity_line(tool_name, arguments, result, active=active),
            max(18, min(summary_cap, width - status_width - 10)),
        )
        detail = _tool_summary(tool_name, arguments)
        phase = self._tool_animation_phase(tool_name, arguments)

        items.append(build_header(summary, phase=phase))

        meta = Text(no_wrap=True, overflow="crop")
        meta.append("    ", style=_BORDER)
        meta.append(tool_name, style=f"bold {accent}" if active else accent)
        if detail and detail != summary:
            meta.append("  ", style=_DIM)
            meta.append(detail, style=_DIM)
        items.append(meta)

        if mode == "diff" and arguments.get("command") == "str_replace":
            old_lines = (arguments.get("old_str", "") or "").splitlines()
            new_lines = (arguments.get("new_str", "") or "").splitlines()
            diff = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=str(arguments.get("path", "before")),
                    tofile=str(arguments.get("path", "after")),
                    lineterm="",
                )
            )
            items.append(Text(""))
            for dl in diff[:16]:
                line = Text(no_wrap=True, overflow="crop")
                padded = f" {dl}".ljust(200)
                if dl.startswith("+") and not dl.startswith("+++"):
                    line.append(padded, style=f"bold {_GREEN} on #102617")
                elif dl.startswith("-") and not dl.startswith("---"):
                    line.append(padded, style=f"bold {_RED} on #2a1014")
                elif dl.startswith("@@"):
                    line.append(padded, style=f"bold {_MAGENTA} on #0f1830")
                else:
                    line.append(padded, style=f"{_SUBTEXT} on {_tool_accent_bg(tool_name)}")
                items.append(line)
            if len(diff) > 16:
                items.append(Text(f"    ... ({len(diff) - 16} more lines)", style=_BORDER))

        handled_structured_result = False
        if result and _is_sequential_thinking_tool(tool_name):
            handled_structured_result = self._append_sequential_thinking_result(
                items,
                arguments,
                result,
                success=success,
            )

        if result and (mode != "diff" or arguments.get("command") != "str_replace") and not handled_structured_result:
            out_lines = result.strip().splitlines()
            shown = out_lines[:8]
            result_style = _SUBTEXT if success else _RED
            items.append(Text(""))
            for ol in shown:
                line = Text()
                line.append("    ", style=_BORDER)
                line.append(ol[:120], style=result_style)
                items.append(line)
            if len(out_lines) > 8:
                items.append(Text(f"    ... ({len(out_lines) - 8} more lines)", style=_BORDER))

        return Group(*items)

    def _render_tool_call_block(
        self,
        tool_name: str,
        arguments: dict,
        result: str | None,
        *,
        success: bool = True,
        active: bool = False,
    ):
        if active:
            return _AnimatedToolCallBlock(
                self,
                tool_name,
                arguments,
                result,
                success=success,
            )
        return self._build_tool_call_block(
            tool_name,
            arguments,
            result,
            success=success,
            active=active,
        )

    @staticmethod
    def _normalize_rendered_text(content: str) -> str:
        return " ".join(content.split()).strip()

    def _should_render_final_result(self, final_result: str) -> bool:
        normalized_final = self._normalize_rendered_text(final_result)
        if not normalized_final or not self.agent_execution:
            return False
        for step in reversed(self.agent_execution.steps):
            if step.llm_response and step.llm_response.content:
                last_response = self._normalize_rendered_text(step.llm_response.content)
                return normalized_final != last_response
        return True

    def _print_turn_end_line(self) -> None:
        if not self.agent_execution:
            return

        if self.agent_execution.final_result and self._should_render_final_result(self.agent_execution.final_result):
            self._write(Text(" "))
            self._write(
                Markdown(self.agent_execution.final_result),
                plain=self.agent_execution.final_result[:200],
            )

        success = self.agent_execution.success
        color = _GREEN if success else _RED
        word = "Done" if success else "Failed"

        line = Text()
        line.append("  * ", style=f"bold {color}")
        line.append(word, style=f"bold {color}")
        line.append(f" . {len(self.agent_execution.steps)} steps", style=_DIM)
        if self.agent_execution.execution_time:
            line.append(f" . {self.agent_execution.execution_time:.1f}s", style=_DIM)
        if self.agent_execution.total_tokens:
            t = self.agent_execution.total_tokens
            line.append(f" . in {t.input_tokens} / out {t.output_tokens}", style=_DIM)

        self._write(line, plain=f"  * {word}")
        self._write(Text(" "))
