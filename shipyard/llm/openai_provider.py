"""OpenAI provider — drop-in replacement proving the abstraction works."""

from __future__ import annotations

import openai

from shipyard.config import settings
from shipyard.llm.provider import LLMResponse, TokenUsage, ToolCall


class OpenAIProvider:
    """LLMProvider implementation backed by OpenAI GPT models."""

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None):
        self._model = model
        self._client = openai.AsyncOpenAI(
            api_key=api_key or settings.openai_api_key,
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        msgs = list(messages)
        if system:
            msgs.insert(0, {"role": "system", "content": system})

        kwargs: dict = {"model": self._model, "messages": msgs}
        if tools:
            # Convert from Anthropic tool format to OpenAI function format
            openai_tools = []
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                })
            kwargs["tools"] = openai_tools

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            import json

            for tc in message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                )

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            usage=TokenUsage(
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
            ),
        )

    def get_model_name(self) -> str:
        return self._model

    def get_context_window_size(self) -> int:
        return 128_000
