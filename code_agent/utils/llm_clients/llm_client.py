# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""LLM Client wrapper for OpenAI, Anthropic, Azure, and OpenRouter APIs."""

import logging
from enum import Enum

logger = logging.getLogger(__name__)

from code_agent.tools.base import Tool
from code_agent.utils.config import ModelConfig
from code_agent.utils.llm_clients.base_client import BaseLLMClient
from code_agent.utils.llm_clients.llm_basics import LLMMessage, LLMResponse
from code_agent.utils.trajectory_recorder import TrajectoryRecorder


class LLMProvider(Enum):
    """Supported LLM providers.

    Providers marked [Responses API] use OpenAI's newer Responses API and are
    OpenAI-specific.  All other providers use the standard Chat Completions API
    and are interchangeable as long as the endpoint is OpenAI-compatible.
    """

    # --- OpenAI native (Responses API, OpenAI-specific) ---
    OPENAI = "openai"           # [Responses API] OpenAI official

    # --- Anthropic native ---
    ANTHROPIC = "anthropic"

    # --- Google native ---
    GOOGLE = "google"

    # --- OpenAI Chat Completions–compatible providers ---
    # All of the entries below use the standard /v1/chat/completions endpoint.
    DOUBAO = "doubao"           # ByteDance Doubao / legacy alias for generic compat
    DEEPSEEK = "deepseek"       # DeepSeek: https://platform.deepseek.com/
    ZHIPU = "zhipu"             # Zhipu AI (GLM series): https://open.bigmodel.cn/
    QWEN = "qwen"               # Alibaba DashScope (Qwen series): https://dashscope.aliyun.com/
    SILICONFLOW = "siliconflow" # SiliconFlow aggregator: https://siliconflow.cn/
    GROQ = "groq"               # Groq (ultra-fast inference): https://console.groq.com/
    MINIMAX = "minimax"         # MiniMax: https://platform.minimaxi.com/

    # --- OpenAI-compatible with custom auth/routing ---
    AZURE = "azure"             # Azure OpenAI Service
    OPENROUTER = "openrouter"   # OpenRouter multi-provider gateway

    # --- Native library (not HTTP Chat Completions) ---
    OLLAMA = "ollama"           # Ollama (local models, uses native ollama SDK): https://ollama.com/


class LLMClient:
    """Main LLM client that supports multiple providers."""

    def __init__(self, model_config: ModelConfig) -> None:
        self.provider: LLMProvider = LLMProvider(model_config.model_provider.provider)
        self.model_config: ModelConfig = model_config

        match self.provider:
            case LLMProvider.OPENAI:
                from .openai_client import OpenAIClient

                self.client: BaseLLMClient = OpenAIClient(model_config)
            case LLMProvider.ANTHROPIC:
                from .anthropic_client import AnthropicClient

                self.client = AnthropicClient(model_config)
            case LLMProvider.GOOGLE:
                from .google_client import GoogleClient

                self.client = GoogleClient(model_config)
            case LLMProvider.AZURE:
                from .azure_client import AzureClient

                self.client = AzureClient(model_config)
            case LLMProvider.OPENROUTER:
                from .openrouter_client import OpenRouterClient

                self.client = OpenRouterClient(model_config)
            case LLMProvider.OLLAMA:
                # Use Ollama's OpenAI-compatible /v1/chat/completions endpoint so that:
                # - tool calling follows the same code path as other compatible providers
                # - requests are visible in Ollama server logs (HTTP)
                from .openai_compat_client import OpenAICompatClient

                self.client = OpenAICompatClient(model_config, self.provider.value)
            case (
                LLMProvider.DOUBAO
                | LLMProvider.DEEPSEEK
                | LLMProvider.ZHIPU
                | LLMProvider.QWEN
                | LLMProvider.SILICONFLOW
                | LLMProvider.GROQ
                | LLMProvider.MINIMAX
            ):
                # All of these expose the standard OpenAI Chat Completions API.
                from .openai_compat_client import OpenAICompatClient

                self.client = OpenAICompatClient(model_config, self.provider.value)

    def set_trajectory_recorder(self, recorder: TrajectoryRecorder | None) -> None:
        """Set the trajectory recorder for the underlying client."""
        self.client.set_trajectory_recorder(recorder)

    def set_chat_history(self, messages: list[LLMMessage]) -> None:
        """Set the chat history."""
        self.client.set_chat_history(messages)

    def chat(
        self,
        messages: list[LLMMessage],
        model_config: ModelConfig,
        tools: list[Tool] | None = None,
        reuse_history: bool = True,
        agent_type: str = None
    ) -> LLMResponse:
        """Send chat messages to the LLM.

        Retry logic and should_retry filtering live inside each provider's
        chat() implementation at the _create_response level.  Exceptions
        propagate to base_agent._run_llm_step for uniform error handling.
        """
        return self.client.chat(messages, model_config, tools, reuse_history, agent_type)
        

    def supports_tool_calling(self, model_config: ModelConfig) -> bool:
        """Check if the current client supports tool calling."""
        return hasattr(self.client, "supports_tool_calling") and self.client.supports_tool_calling(
            model_config
        )
