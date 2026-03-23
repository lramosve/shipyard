"""Claude provider via the Anthropic SDK."""

from __future__ import annotations

import json

import anthropic

from shipyard.config import settings
from shipyard.llm.provider import LLMResponse, TokenUsage, ToolCall


class AnthropicProvider:
    """LLMProvider implementation backed by Claude."""

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self._model = model or settings.llm_model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or settings.anthropic_api_key,
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)

        content_text: str | None = None
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_text = block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else json.loads(block.input),
                    )
                )

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "end_turn",
            usage=TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
        )

    def get_model_name(self) -> str:
        return self._model

    def get_context_window_size(self) -> int:
        return 200_000
