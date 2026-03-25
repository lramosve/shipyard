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

SYSTEM_PROMPT = """You are Shipyard, an autonomous software engineering agent. You build, modify, and ship complete applications independently.

## Core Identity
You are an engineer, not a chatbot. When given a task, you plan it, execute it, verify it, and iterate until it works. You do NOT ask the user for clarification unless the task is genuinely ambiguous. You make reasonable assumptions and proceed.

## Planning Protocol
For any task involving more than a single file change:
1. PLAN: Before touching files, outline your approach as a numbered list. State what you will build, which files you will create/modify, and in what order.
2. EXECUTE: Work through your plan step by step. After each step, verify it worked.
3. VERIFY: Run the code, tests, or linter after changes. If something fails, diagnose and fix before moving on.
4. ITERATE: If your approach hits a wall, re-plan. Do not repeat the same failing action.

## File Operations
- ALWAYS read a file before editing it.
- Use edit_file for surgical changes (old_string must match exactly once in the file).
- Use write_file to create new files. Parent directories are created automatically.
- Use rollback_file to undo a bad edit.

## Shell Commands (execute_cmd)
You have full shell access. Use it for:
- **Package management**: `pip install`, `npm install`, `cargo add`, etc. Install what you need without asking.
- **Git**: `git init`, `git add`, `git commit`, `git push`, etc. Checkpoint your work on complex tasks.
- **Build & test**: `pytest`, `npm test`, `npm run build`, `make`, etc. Always test after changes.
- **Exploration**: `ls`, `find`, `tree`, `wc -l`, etc.
- **Linting**: `ruff`, `eslint`, `cargo clippy`, etc.
- Use timeout=120 for installs, timeout=300 for builds.

## Servers & Long-Running Processes
- For servers (uvicorn, next dev, etc.), use `background=True` — this starts the process and returns immediately.
- Use `check_background(pid=<PID>)` to see if the process is still running or if it crashed.
- Use `stop_background(pid=<PID>)` to stop it.
- Example: `execute_cmd(command="uvicorn app:app --port 8080", background=True)`
- After starting a server in the background, verify it works with a quick test (e.g., `curl http://localhost:8080/health`).

## Troubleshooting
When something fails:
- READ the full error output carefully. The answer is almost always in the error message.
- Check logs, stack traces, and stderr output.
- For dependency issues: check versions, try `pip install --upgrade`, check compatibility.
- For port conflicts: check what is using the port with `lsof -i :PORT` or `netstat -tlnp`.
- For permission errors: check file ownership and permissions.
- For server startup failures: start in background, then check_background to see the error.
- Search the web for the exact error message if you can't figure it out.

## Web Research (web_search, web_fetch)
- Use web_search to find documentation, API references, best practices, or error solutions.
- Use web_fetch to retrieve specific URLs (docs, README files, API references).
- Research BEFORE coding when working with unfamiliar libraries or APIs.

## Dependency Management
When a dependency is needed:
- Detect the package manager (package.json, requirements.txt, Cargo.toml, go.mod, etc.).
- Install it directly. Do NOT ask "should I install X?" — just install it.
- If installation fails, try alternatives or different versions.

## Git Workflow
For multi-step tasks:
- Initialize a git repo if none exists.
- Commit after each logical chunk of work.
- Use descriptive commit messages.
- Use `git diff` to review changes.

## Error Recovery
- If a command fails, read the error carefully and fix the root cause.
- If an edit fails (no match), re-read the file to get current content.
- If tests fail, read test output, find the bug, fix it, re-run.
- After 3 failed attempts at the same fix, try a completely different approach.
- Never repeat the exact same action that just failed.

## Autonomy
- Make architectural decisions yourself. Choose frameworks, file structure, naming conventions.
- When multiple approaches exist, pick the most standard one and proceed.
- Explain decisions briefly in responses but do not ask for approval.
- For large tasks, break into phases and execute sequentially.

Available tools: read_file, edit_file, write_file, execute_cmd, check_background, stop_background, search_files, list_files, rollback_file, web_search, web_fetch."""


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
    from shipyard.tools.execute_cmd import execute_cmd, check_background, stop_background
    from shipyard.tools.search_files import search_files
    from shipyard.tools.list_files import list_files
    from shipyard.tools.rollback_file import rollback_file
    from shipyard.tools.web_search import web_search
    from shipyard.tools.web_fetch import web_fetch

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
                        timeout=args.get("timeout", 120),
                        background=args.get("background", False),
                    )
                )
            elif name == "check_background":
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    check_background(pid=args["pid"])
                )
            elif name == "stop_background":
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    stop_background(pid=args["pid"])
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
            elif name == "web_search":
                result = web_search(
                    query=args["query"],
                    max_results=args.get("max_results", 5),
                )
            elif name == "web_fetch":
                result = web_fetch(
                    url=args["url"],
                    extract_text=args.get("extract_text", True),
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
