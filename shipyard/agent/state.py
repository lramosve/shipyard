"""LangGraph agent state definition."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    file_read_tracker: dict[str, float]  # path -> mtime at last read
    injected_context: list[dict]  # [{"type": str, "source": str, "content": str}]
    working_directory: str
