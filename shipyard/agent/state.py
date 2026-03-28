"""LangGraph agent state definition."""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def _merge_file_trackers(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    """Merge file-read trackers from parallel branches, keeping latest mtime."""
    merged = dict(left)
    for path, mtime in right.items():
        if path not in merged or mtime > merged[path]:
            merged[path] = mtime
    return merged


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    file_read_tracker: Annotated[dict[str, float], _merge_file_trackers]
    injected_context: list[dict]  # [{"type": str, "source": str, "content": str}]
    working_directory: str
    consecutive_errors: int  # track repeated failures for circuit-breaking
    # Multi-agent supervisor fields
    architecture_plan: str  # structured plan produced by the Architect worker
    architecture_plan_json: str  # JSON-serialized ArchitecturePlan (from plan_schema.py)
    current_phase: str  # "architect" | "coder" | "test_review" | "fix" | "done"
    review_issues: Annotated[list[str], add]  # issues from Reviewer (reducer for parallel merge)
    iteration_count: int  # tracks review-fix cycles (capped at 3)
    previous_issues: list[str]  # issues from the previous fix cycle (for detecting no-progress loops)
