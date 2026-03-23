"""LangGraph node functions for the agent loop."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from shipyard.agent.state import AgentState
from shipyard.context.injection import format_injected_context
from shipyard.tools.base import FileReadTracker

SYSTEM_PROMPT = """You are Shipyard, an autonomous coding agent. You help users by reading, editing, and creating code files.

Key behaviors:
- Always read a file before editing it.
- Make surgical, targeted edits using edit_file — do not rewrite entire files.
- When an edit fails, re-read the file and retry with the correct text.
- Run commands to verify your changes work (e.g., run tests, linters).
- Be concise in your responses. Explain what you changed and why.

Available tools: read_file, edit_file, write_file, execute_cmd, search_files, list_files."""


def build_system_prompt(state: AgentState) -> str:
    """Build the system prompt including any injected context."""
    parts = [SYSTEM_PROMPT]
    injected = format_injected_context(state.get("injected_context", []))
    if injected:
        parts.append("\n\n# Injected Context\n\n" + injected)
    return "\n".join(parts)


def call_llm(state: AgentState, model: Any) -> dict:
    """Call the LLM with current messages and tools."""
    system = build_system_prompt(state)

    # Prepend system message if not already there
    messages = list(state["messages"])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages.insert(0, SystemMessage(content=system))
    else:
        messages[0] = SystemMessage(content=system)

    response = model.invoke(messages)

    return {"messages": [response]}


def execute_tools(state: AgentState) -> dict:
    """Execute tool calls from the last AI message."""
    from shipyard.tools.read_file import read_file
    from shipyard.tools.edit_file import edit_file
    from shipyard.tools.write_file import write_file
    from shipyard.tools.execute_cmd import execute_cmd
    from shipyard.tools.search_files import search_files
    from shipyard.tools.list_files import list_files
    import asyncio

    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        return {"messages": []}

    tracker = FileReadTracker.from_dict(state.get("file_read_tracker", {}))
    working_dir = state.get("working_directory", ".")

    tool_messages: list[ToolMessage] = []

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
                )
            elif name == "write_file":
                result = write_file(
                    file_path=args["file_path"],
                    content=args["content"],
                    tracker=tracker,
                )
            elif name == "execute_cmd":
                # Run async tool in sync context
                result = asyncio.get_event_loop().run_until_complete(
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
            else:
                result = _make_error(f"Unknown tool: {name}")
        except Exception as e:
            result = _make_error(f"Tool '{name}' raised an exception: {e}")

        tool_messages.append(
            ToolMessage(
                content=result.output,
                tool_call_id=tool_call_id,
                status="error" if result.is_error else "success",
            )
        )

    return {
        "messages": tool_messages,
        "file_read_tracker": tracker.to_dict(),
    }


def should_continue(state: AgentState) -> str:
    """Decide whether to continue the agent loop or stop."""
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "execute_tools"
    return "end"


def _make_error(msg: str):
    from shipyard.tools.base import ToolResult
    return ToolResult(output=msg, is_error=True)
