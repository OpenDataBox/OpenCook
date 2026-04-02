# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Agent package exports.

Keep imports resilient so lightweight modules can import agent metadata without
requiring every optional runtime dependency to be installed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _optional_symbol(module_name: str, symbol_name: str):
    try:
        module = __import__(module_name, fromlist=[symbol_name])
        return getattr(module, symbol_name)
    except Exception as exc:
        logger.debug("Skipping optional agent export %s.%s: %s", module_name, symbol_name, exc)
        return None


Agent = _optional_symbol("code_agent.agent.agent", "Agent")
BaseAgent = _optional_symbol("code_agent.agent.base_agent", "BaseAgent")
CodeAgent = _optional_symbol("code_agent.agent.code_agent", "CodeAgent")
PlanAgent = _optional_symbol("code_agent.subagents.plan_agent", "PlanAgent")
TestAgent = _optional_symbol("code_agent.subagents.test_agent", "TestAgent")

__all__ = [
    name
    for name, value in (
        ("Agent", Agent),
        ("BaseAgent", BaseAgent),
        ("PlanAgent", PlanAgent),
        ("CodeAgent", CodeAgent),
        ("TestAgent", TestAgent),
    )
    if value is not None
]
