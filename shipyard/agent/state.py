"""LangGraph agent state definition."""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    file_read_tracker: dict[str, float]  # path -> mtime at last read
    injected_context: list[dict]  # [{"type": str, "source": str, "content": str}]
    working_directory: str
    consecutive_errors: int  # track repeated failures for circuit-breaking
    # Multi-agent supervisor fields
    architecture_plan: str  # structured plan produced by the Architect worker
    current_phase: str  # "architect" | "coder" | "test_review" | "fix" | "done"
    review_issues: Annotated[list[str], add]  # issues from Reviewer (reducer for parallel merge)
    iteration_count: int  # tracks review-fix cycles (capped at 3)
