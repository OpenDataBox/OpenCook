# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""
Ollama API client wrapper with tool integration
"""

import json
import os
import uuid
try:
    from typing import override
except ImportError:
    def override(func):
        return func

import openai
from ollama import Client as OllamaSDKClient  # pyright: ignore[reportUnknownVariableType]
from openai.types.responses import (
    FunctionToolParam,
    ResponseFunctionToolCallParam,
    ResponseInputParam,
)
from openai.types.responses.response_input_param import FunctionCallOutput

from code_agent.tools.base import Tool, ToolCall, ToolResult
from code_agent.utils.config import ModelConfig
from code_agent.utils.llm_clients.base_client import BaseLLMClient
from code_agent.utils.llm_clients.llm_basics import LLMMessage, LLMResponse
from code_agent.utils.llm_clients.retry_utils import retry_with


class OllamaClient(BaseLLMClient):
    def __init__(self, model_config: ModelConfig):
        super().__init__(model_config)

        configured_base_url = (
            model_config.model_provider.base_url
            or os.getenv("OLLAMA_HOST")
            or "http://localhost:11434"
        )
        # The OpenAI-compatible endpoint often uses /v1, while ollama.Client
        # expects the service host root.
        if configured_base_url.endswith("/v1"):
            configured_base_url = configured_base_url[:-3]

        self.client: openai.OpenAI = openai.OpenAI(
            # by default ollama doesn't require any api key. It should set to be "ollama".
            api_key=self.api_key,
            base_url=(configured_base_url.rstrip("/") + "/v1"),
        )
        self.ollama_client = OllamaSDKClient(host=configured_base_url)

        self.message_history: ResponseInputParam = []

    @override
    def should_retry(self, exc: Exception) -> bool:
        """Permanent 4xx client errors must not be retried."""
        try:
            import ollama
            if isinstance(exc, ollama.ResponseError):
                status = getattr(exc, "status_code", None)
                if status in (400, 401, 403, 404):
                    return False
        except ImportError:
            pass
        return True

    @override
    def set_chat_history(self, messages: list[LLMMessage]) -> None:
        self.message_history = self.parse_messages(messages)

    def _create_ollama_response(
        self,
        model_config: ModelConfig,
        tool_schemas: list[FunctionToolParam] | None,
    ):
        """Create a response using Ollama API. This method will be decorated with retry logic."""
        tools_param = None
        if tool_schemas:
            tools_param = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool["parameters"],
                    },
                }
                for tool in tool_schemas
            ]
        return self.ollama_client.chat(
            messages=self.message_history,
            model=model_config.model,
            tools=tools_param,
        )

    @override
    def chat(
        self,
        messages: list[LLMMessage],
        model_config: ModelConfig,
        tools: list[Tool] | None = None,
        reuse_history: bool = True,
        agent_type: str | None = None,
    ) -> LLMResponse:
        """
        A rewritten version of ollama chan
        """
        msgs: ResponseInputParam = self.parse_messages(messages)

        tool_schemas = None
        if tools:
            tool_schemas = [
                FunctionToolParam(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.get_input_schema(),
                    strict=True,
                    type="function",
                )
                for tool in tools
            ]

        # Snapshot history before modification so it can be rolled back if the
        # asyncio task is cancelled while this thread is blocked on the HTTP call.
        history_snapshot = self.message_history
        if reuse_history:
            self.message_history = self.message_history + msgs
        else:
            self.message_history = msgs

        # Apply retry decorator to the API call
        retry_decorator = retry_with(
            func=self._create_ollama_response,
            provider_name="Ollama",
            max_retries=model_config.max_retries,
            cancel_event=self.cancel_flag,
            should_retry=self.should_retry,
        )
        response = retry_decorator(model_config, tool_schemas)

        # If the asyncio task was cancelled while blocked, restore history and
        # return without recording trajectory or appending the assistant reply.
        if self.is_cancelled():
            self.message_history = history_snapshot
            from code_agent.utils.llm_clients.llm_basics import LLMResponse as _R
            return _R(content="", tool_calls=None, finish_reason="cancelled")

        content = ""
        tool_calls: list[ToolCall] = []

        if response.message.tool_calls:
            for tool in response.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        call_id=self._id_generator(),
                        name=tool.function.name,
                        arguments=dict(tool.function.arguments),
                        id=self._id_generator(),
                    )
                )
        else:
            # consider response is not a tool call
            content = str(response.message.content)

        llm_response = LLMResponse(
            content=content,
            usage=None,
            model=model_config.model,
            finish_reason=None,  # seems can't get finish reason will check docs soon
            tool_calls=tool_calls if len(tool_calls) > 0 else None,
        )

        if self.trajectory_recorder:
            self.trajectory_recorder.record_llm_interaction(
                agent_type=agent_type,
                messages=messages,
                response=llm_response,
                provider="ollama",
                model=model_config.model,
                tools=tools,
            )

        return llm_response

    def parse_messages(self, messages: list[LLMMessage]) -> ResponseInputParam:
        """
        Ollama parse messages should be compatible with openai handling
        """
        openai_messages: ResponseInputParam = []
        for msg in messages:
            if msg.tool_result:
                openai_messages.append(self.parse_tool_call_result(msg.tool_result))
            elif msg.tool_call:
                openai_messages.append(self.parse_tool_call(msg.tool_call))
            else:
                if not msg.content:
                    raise ValueError("Message content is required")
                if msg.role == "system":
                    openai_messages.append({"role": "system", "content": msg.content})
                elif msg.role == "user":
                    openai_messages.append({"role": "user", "content": msg.content})
                elif msg.role == "assistant":
                    openai_messages.append({"role": "assistant", "content": msg.content})
                else:
                    raise ValueError(f"Invalid message role: {msg.role}")
        return openai_messages

    def parse_tool_call(self, tool_call: ToolCall) -> ResponseFunctionToolCallParam:
        """Parse the tool call from the LLM response."""
        return ResponseFunctionToolCallParam(
            call_id=tool_call.call_id,
            name=tool_call.name,
            arguments=json.dumps(tool_call.arguments),
            type="function_call",
        )

    def parse_tool_call_result(self, tool_call_result: ToolResult) -> FunctionCallOutput:
        """Parse the tool call result from the LLM response."""
        result: str = ""
        if tool_call_result.result:
            result = result + tool_call_result.result + "\n"
        if tool_call_result.error:
            result += tool_call_result.error
        result = result.strip()

        return FunctionCallOutput(
            call_id=tool_call_result.call_id,
            id=tool_call_result.id,
            output=result,
            type="function_call_output",
        )

    def _id_generator(self) -> str:
        """Generate a random ID string"""
        return str(uuid.uuid4())
