"""LangGraph node functions for the agent loop."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from shipyard.agent.compaction import compact_messages, needs_compaction
from shipyard.agent.state import AgentState
from shipyard.config import settings
from shipyard.context.injection import format_injected_context
from shipyard.tools.base import FileReadTracker
from shipyard.tools.snapshots import FileSnapshotStore
from shipyard.utils.retry import with_retry

logger = logging.getLogger(__name__)

# Module-level snapshot store shared across invocations within a session
_snapshot_store = FileSnapshotStore()

SYSTEM_PROMPT = """You are Shipyard, an autonomous coding agent. You help users by reading, editing, and creating code files.

Key behaviors:
- Always read a file before editing it.
- Make surgical, targeted edits using edit_file — do not rewrite entire files.
- When an edit fails, re-read the file and retry with the correct text.
- Run commands to verify your changes work (e.g., run tests, linters).
- Use rollback_file to undo a bad edit if needed.
- Be concise in your responses. Explain what you changed and why.

Available tools: read_file, edit_file, write_file, execute_cmd, search_files, list_files, rollback_file."""


def get_snapshot_store() -> FileSnapshotStore:
    """Get the module-level snapshot store."""
    return _snapshot_store


def reset_snapshot_store():
    """Reset the snapshot store (for session resets)."""
    global _snapshot_store
    _snapshot_store = FileSnapshotStore()


def build_system_prompt(state: AgentState) -> str:
    """Build the system prompt including any injected context."""
    parts = [SYSTEM_PROMPT]
    injected = format_injected_context(state.get("injected_context", []))
    if injected:
        parts.append("\n\n# Injected Context\n\n" + injected)
    return "\n".join(parts)


def call_llm(state: AgentState, model: Any) -> dict:
    """Call the LLM with current messages and tools. Includes compaction and retry."""
    system = build_system_prompt(state)

    messages = list(state["messages"])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages.insert(0, SystemMessage(content=system))
    else:
        messages[0] = SystemMessage(content=system)

    # Compact if approaching context window limit
    if needs_compaction(messages):
        messages = compact_messages(messages, model)
        logger.info("Context compacted before LLM call")

    # Retry on transient API failures
    @with_retry(max_retries=3, base_delay=2.0)
    def _invoke():
        return model.invoke(messages)

    response = _invoke()

    return {"messages": [response]}


def execute_tools(state: AgentState) -> dict:
    """Execute tool calls from the last AI message."""
    from shipyard.tools.read_file import read_file
    from shipyard.tools.edit_file import edit_file
    from shipyard.tools.write_file import write_file
    from shipyard.tools.execute_cmd import execute_cmd
    from shipyard.tools.search_files import search_files
    from shipyard.tools.list_files import list_files
    from shipyard.tools.rollback_file import rollback_file

    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        return {"messages": []}

    tracker = FileReadTracker.from_dict(state.get("file_read_tracker", {}))
    snapshot_store = get_snapshot_store()
    working_dir = state.get("working_directory", ".")

    tool_messages: list[ToolMessage] = []
    error_count = 0

    for tool_call in last_message.tool_calls:
        name = tool_call["name"]
        args = tool_call["args"]
        tool_call_id = tool_call["id"]

        try:
            if name == "read_file":
                result = read_file(
                    file_path=args["file_path"],
                    start_line=args.get("start_line"),
                    end_line=args.get("end_line"),
                    tracker=tracker,
                )
            elif name == "edit_file":
                result = edit_file(
                    file_path=args["file_path"],
                    old_string=args["old_string"],
                    new_string=args["new_string"],
                    tracker=tracker,
                    snapshot_store=snapshot_store,
                )
            elif name == "write_file":
                result = write_file(
                    file_path=args["file_path"],
                    content=args["content"],
                    tracker=tracker,
                    snapshot_store=snapshot_store,
                )
            elif name == "execute_cmd":
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    execute_cmd(
                        command=args["command"],
                        timeout=args.get("timeout", 30),
                    )
                )
            elif name == "search_files":
                result = search_files(
                    pattern=args["pattern"],
                    directory=args.get("directory", working_dir),
                    file_glob=args.get("file_glob", "*"),
                )
            elif name == "list_files":
                result = list_files(
                    directory=args.get("directory", working_dir),
                    pattern=args.get("pattern"),
                    recursive=args.get("recursive", False),
                )
            elif name == "rollback_file":
                result = rollback_file(
                    file_path=args["file_path"],
                    version=args.get("version", -1),
                    snapshot_store=snapshot_store,
                    tracker=tracker,
                )
            else:
                result = _make_error(f"Unknown tool: {name}")
        except FileNotFoundError as e:
            result = _make_error(f"File not found: {e}")
        except PermissionError as e:
            result = _make_error(f"Permission denied: {e}")
        except Exception as e:
            result = _make_error(f"Tool '{name}' raised an exception: {type(e).__name__}: {e}")

        if result.is_error:
            error_count += 1

        tool_messages.append(
            ToolMessage(
                content=result.output,
                tool_call_id=tool_call_id,
                status="error" if result.is_error else "success",
            )
        )

    # Track consecutive errors for circuit-breaking
    prev_errors = state.get("consecutive_errors", 0)
    new_errors = prev_errors + error_count if error_count > 0 else 0

    return {
        "messages": tool_messages,
        "file_read_tracker": tracker.to_dict(),
        "consecutive_errors": new_errors,
    }


def should_continue(state: AgentState) -> str:
    """Decide whether to continue the agent loop or stop."""
    # Circuit-break on repeated failures
    if state.get("consecutive_errors", 0) >= 5:
        logger.warning("Circuit breaker: 5+ consecutive tool errors, stopping agent")
        return "end"

    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "execute_tools"
    return "end"


def _make_error(msg: str):
    from shipyard.tools.base import ToolResult
    return ToolResult(output=msg, is_error=True)
