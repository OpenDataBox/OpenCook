# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Client for any OpenAI Chat Completions–compatible provider.

Any provider that exposes the standard ``/v1/chat/completions`` endpoint can be
used by setting ``provider: <name>`` in the config, where ``<name>`` matches one
of the values registered in ``LLMProvider`` (llm_client.py).

Providers that need custom headers or non-standard behaviour (e.g. Azure, OpenRouter)
should keep their own dedicated ProviderConfig subclass instead of using this class.
"""

import openai

from code_agent.utils.config import ModelConfig
from code_agent.utils.llm_clients.openai_compatible_base import (
    OpenAICompatibleClient,
    ProviderConfig,
)


class OpenAICompatProvider(ProviderConfig):
    """ProviderConfig for any provider that supports the OpenAI Chat Completions API."""

    def __init__(self, provider_name: str) -> None:
        self._provider_name = provider_name

    def create_client(
        self, api_key: str, base_url: str | None, api_version: str | None
    ) -> openai.OpenAI:
        return openai.OpenAI(base_url=base_url, api_key=api_key)

    def get_service_name(self) -> str:
        return self._provider_name.capitalize()

    def get_provider_name(self) -> str:
        return self._provider_name

    def get_extra_headers(self) -> dict[str, str]:
        return {}

    def supports_tool_calling(self, model_name: str) -> bool:
        return True


class OpenAICompatClient(OpenAICompatibleClient):
    """Client for any OpenAI Chat Completions–compatible provider."""

    def __init__(self, model_config: ModelConfig, provider_name: str) -> None:
        super().__init__(model_config, OpenAICompatProvider(provider_name))
