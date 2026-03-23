"""LLM provider abstraction — swap Claude for OpenAI by changing one config value."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = []
    stop_reason: str = "end_turn"
    usage: TokenUsage = TokenUsage()


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol that any LLM backend must implement."""

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
    ) -> LLMResponse: ...

    def get_model_name(self) -> str: ...

    def get_context_window_size(self) -> int: ...
