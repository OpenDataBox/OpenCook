# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Session data models."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
import uuid


@dataclass
class SessionMeta:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    cwd: str = ""
    database: str = "sqlite"
    model: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    archived: bool = False


@dataclass
class SessionTurn:
    turn_index: int
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    user_input: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    finished_at: str = ""
    success: bool = False
    trajectory_file: str = ""
    patch_file: str = ""


@dataclass
class TranscriptMessage:
    role: Literal["user", "assistant", "summary"]
    content: str
    turn_index: int = -1
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
