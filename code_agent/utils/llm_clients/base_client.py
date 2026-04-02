# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import threading
from abc import ABC, abstractmethod

from code_agent.tools.base import Tool
from code_agent.utils.config import ModelConfig
from code_agent.utils.llm_clients.llm_basics import LLMMessage, LLMResponse
from code_agent.utils.trajectory_recorder import TrajectoryRecorder


class BaseLLMClient(ABC):
    """Base class for LLM clients."""

    def __init__(self, model_config: ModelConfig):
        self.api_key: str = model_config.model_provider.api_key
        self.base_url: str | None = model_config.model_provider.base_url
        self.api_version: str | None = model_config.model_provider.api_version
        self.trajectory_recorder: TrajectoryRecorder | None = None  # TrajectoryRecorder instance
        # Set by BaseAgent before each run_in_executor call; checked inside chat()
        # after the blocking HTTP call returns so that message_history mutations
        # and trajectory writes are skipped for cancelled turns.
        self.cancel_flag: threading.Event | None = None

    def is_cancelled(self) -> bool:
        """Return True if the asyncio task was cancelled while this thread was blocked."""
        return self.cancel_flag is not None and self.cancel_flag.is_set()

    def set_trajectory_recorder(self, recorder: TrajectoryRecorder | None) -> None:
        """Set the trajectory recorder for this client."""
        self.trajectory_recorder = recorder

    @abstractmethod
    def set_chat_history(self, messages: list[LLMMessage]) -> None:
        """Set the chat history."""
        pass

    @abstractmethod
    def chat(
        self,
        messages: list[LLMMessage],
        model_config: ModelConfig,
        tools: list[Tool] | None = None,
        reuse_history: bool = True,
        agent_type: str | None = None,
    ) -> LLMResponse:
        """Send chat messages to the LLM."""
        pass

    def should_retry(self, exc: Exception) -> bool:
        """Return False for permanent errors that must not be retried.

        Override in provider subclasses to filter provider-specific 4xx errors
        (e.g. Bad Request, Unauthorized) that will never succeed on retry.
        The default allows retrying any exception.
        """
        return True

    def supports_tool_calling(self, model_config: ModelConfig) -> bool:
        """Check if the current model supports tool calling."""
        return model_config.supports_tool_calling
