# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Tool exports and registry.

Keep optional tool imports resilient so the CLI and other lightweight modules
can import package metadata even when some heavy tool dependencies are absent.
"""

from __future__ import annotations

import logging

from code_agent.tools.base import Tool, ToolCall, ToolExecutor, ToolResult
logger = logging.getLogger(__name__)


def _optional_tool(
    module_name: str,
    class_name: str,
) -> type[Tool] | None:
    try:
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)
    except Exception as exc:
        logger.debug("Skipping optional tool %s.%s: %s", module_name, class_name, exc)
        return None


BashTool = _optional_tool("code_agent.tools.bash_tool", "BashTool")
TextEditorTool = _optional_tool("code_agent.tools.edit_tool", "TextEditorTool")
JSONEditTool = _optional_tool("code_agent.tools.json_edit_tool", "JSONEditTool")
SequentialThinkingTool = _optional_tool("code_agent.tools.sequential_thinking_tool", "SequentialThinkingTool")
TaskDoneTool = _optional_tool("code_agent.tools.task_done_tool", "TaskDoneTool")
CKGTool = _optional_tool("code_agent.tools.ckg_tool", "CKGTool")
DatabaseVerifyTool = _optional_tool("code_agent.tools.database_verify_tool", "DatabaseVerifyTool")
DatabaseExecuteTool = _optional_tool("code_agent.tools.database_execute_tool", "DatabaseExecuteTool")
UnderstandTool = _optional_tool("code_agent.tools.understand_tool", "UnderstandTool")
DepTool = _optional_tool("code_agent.tools.dep_tool", "DepTool")
PlanSubagentTool = _optional_tool("code_agent.tools.plan_subagent_tool", "PlanSubagentTool")
TestSubagentTool = _optional_tool("code_agent.tools.test_subagent_tool", "TestSubagentTool")
SkillTool = _optional_tool("code_agent.tools.skill_tool", "SkillTool")

__all__ = [
    "Tool",
    "ToolResult",
    "ToolCall",
    "ToolExecutor",
]

for optional_name, optional_value in (
    ("BashTool", BashTool),
    ("TextEditorTool", TextEditorTool),
    ("JSONEditTool", JSONEditTool),
    ("SequentialThinkingTool", SequentialThinkingTool),
    ("TaskDoneTool", TaskDoneTool),
    ("CKGTool", CKGTool),
    ("DatabaseVerifyTool", DatabaseVerifyTool),
    ("DatabaseExecuteTool", DatabaseExecuteTool),
    ("UnderstandTool", UnderstandTool),
    ("DepTool", DepTool),
    ("PlanSubagentTool", PlanSubagentTool),
    ("TestSubagentTool", TestSubagentTool),
    ("SkillTool", SkillTool),
):
    if optional_value is not None:
        __all__.append(optional_name)

tools_registry: dict[str, type[Tool]] = {}

for tool_name, tool_cls in (
    ("bash", BashTool),
    ("str_replace_based_edit_tool", TextEditorTool),
    ("json_edit_tool", JSONEditTool),
    ("sequentialthinking", SequentialThinkingTool),
    ("task_done", TaskDoneTool),
    ("ckg", CKGTool),
    ("database_verify", DatabaseVerifyTool),
    ("database_execute", DatabaseExecuteTool),
    ("understand_toolkit", UnderstandTool),
    ("dep_toolkit", DepTool),
    ("plan_subagent", PlanSubagentTool),
    ("test_subagent", TestSubagentTool),
    ("skill", SkillTool),
):
    if tool_cls is not None:
        tools_registry[tool_name] = tool_cls
