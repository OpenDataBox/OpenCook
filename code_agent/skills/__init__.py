# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Public API of the code_agent.skills package.

All consumers (code_agent.py, skill_tool.py, etc.) should import from this
module rather than directly from the sub-modules.
"""

from code_agent.skills.model import (
    SkillLookupResult,
    SkillPackage,
    SkillResourceEntry,
    SkillSourceScope,
)
from code_agent.skills.manager import SkillManager

__all__ = [
    "SkillLookupResult",
    "SkillPackage",
    "SkillResourceEntry",
    "SkillSourceScope",
    "SkillManager",
]
