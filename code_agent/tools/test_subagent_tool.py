# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from typing import override
except ImportError:
    def override(func):
        return func

from code_agent.tools.base import Tool, ToolCallArguments, ToolExecResult, ToolParameter

if TYPE_CHECKING:
    from code_agent.agent.code_agent import CodeAgent


class TestSubagentTool(Tool):
    """Perform three-level validations (i.e., single-file syntax checking, cross-file compliance
    checking, and semantic verification delegation) progressively for the intended code synthesis task."""

    def __init__(self, model_provider: str | None = None) -> None:
        super().__init__(model_provider)
        self._codeagent: CodeAgent | None = None  # set by CodeAgent.new_task()

    @override
    def get_model_provider(self) -> str | None:
        return self._model_provider

    @override
    def get_name(self) -> str:
        return "test_subagent"

    @override
    def get_description(self) -> str:
        return (
            "Perform three-level validations (i.e., single-file syntax checking, "
            "cross-file compliance checking, and semantic verification delegation) progressively "
            "for the intended code synthesis task."
        )

    @override
    def get_parameters(self) -> list[ToolParameter]:
        return []

    @override
    async def execute(self, arguments: ToolCallArguments) -> ToolExecResult:
        if self._codeagent is None or self._codeagent.test_agent is None:
            return ToolExecResult(
                error="TestSubagentTool: _codeagent not set or has no test_agent.",
                error_code=-1,
            )

        test_agent = self._codeagent.test_agent.agent
        task = self._codeagent._task
        extra_args = self._codeagent._extra_args

        return await test_agent.run_verification(task, extra_args)
