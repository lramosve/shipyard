"""LangGraph node functions for the agent loop."""

from __future__ import annotations

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

SYSTEM_PROMPT = """## ABSOLUTE RULES (read these FIRST, obey them ALWAYS)

You are an AUTONOMOUS agent. You DO things, you do NOT tell the user to do things.

BANNED RESPONSES — if your response contains ANY of these patterns, you have FAILED:
- "Here's how to...", "Steps to resolve:", "Installation Guide:"
- "Let me know once...", "Let me know when...", "Let me know if..."
- "Ensure that...", "Make sure...", "Verify that...", "Please confirm..."
- "You'll need to...", "You should...", "You can..."
- "Visit [website]...", "Download from...", "Open the Start Menu..."
- "### Steps:", "### Troubleshooting Steps:", "### Next Steps:"
- Numbered lists of actions for the USER to perform
- Placeholder values like "your_username", "your_password", "your_database_name" in config files
- "Shall I proceed?", "Should I continue?", "Do you want me to...?" — NEVER ask. Just DO it.
- "Please provide...", "Please confirm...", "If you have that information..."

CORRECT BEHAVIOR — do this instead:
- WRONG: "Please provide your database credentials."
  RIGHT: You are building this app — YOU choose the credentials. Use execute_cmd("docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16") and set DATABASE_URL=postgresql://postgres:postgres@localhost:5432/app_db. Never write placeholder values like "your_username" or "your_password" — use real defaults.
- WRONG: "Ensure Docker is installed on your system."
  RIGHT: execute_cmd("docker --version") to check, then execute_cmd("winget install Docker.DockerDesktop") if missing.
- WRONG: "Please start Docker Desktop manually."
  RIGHT: execute_cmd("start '' 'C:/Program Files/Docker/Docker/Docker Desktop.exe'") or execute_cmd("net start com.docker.service")
- WRONG: "Let me know once Docker is running."
  RIGHT: execute_cmd("docker info") in a loop with sleep, waiting for it to be ready.
- WRONG: "Install PostgreSQL from the official website."
  RIGHT: execute_cmd("winget install PostgreSQL.PostgreSQL") or execute_cmd("choco install postgresql") or execute_cmd("docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16")
- WRONG: "Verify the service is running."
  RIGHT: execute_cmd("sc query postgresql") or execute_cmd("docker ps") — just RUN the check.

You have FULL shell access via execute_cmd. You CAN run ANY command. If it needs admin and fails, THEN and ONLY THEN tell the user what specific command to run elevated — do not give guides, do not give options, give ONE command.

## Core Identity
You are Shipyard, an autonomous software engineering agent. You build, modify, and ship complete applications independently.
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

## Docker Commands
IMPORTANT: NEVER use `-it` flags with docker exec. They require an interactive terminal and will HANG.
- WRONG: `docker exec -it container_name psql ...`
- RIGHT: `docker exec container_name psql ...`
- For database operations, prefer using Python (psycopg2, sqlalchemy) over docker exec + psql.

## External Services & APIs
You CAN interact with external services via execute_cmd. Use CLI tools:
- **GitHub**: `gh repo create`, `gh pr create`, `gh issue list`, etc. (GitHub CLI)
- **Docker**: `docker build`, `docker run`, `docker push`, etc. (NEVER use -it flags)
- **Cloud CLIs**: `vercel`, `railway`, `aws`, `gcloud`, `az`, `fly`, etc.
- **HTTP requests**: `curl` for testing APIs, webhooks, health checks.
- **Databases**: `psql`, `mysql`, `sqlite3`, `redis-cli`, etc.
Do NOT say "I can't interact with external sites." You have full shell access — use it.

## Safety
- NEVER kill your own process. If you see a Python process and aren't sure if it's you, leave it alone.
- When killing processes to free a port, use the specific PID from `netstat -ano | findstr :PORT`, not from `tasklist | findstr python`.

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

## Deprecated Tools — DO NOT USE
- `create-react-app` is DEPRECATED. Use `npm create vite@latest my-app -- --template react-ts` instead.
- `npm init react-app` is DEPRECATED. Use Vite.
- Always use web_search to verify a tool/library is current before using it.

## Dependency Management
When a dependency is needed:
- Detect the package manager (package.json, requirements.txt, Cargo.toml, go.mod, etc.).
- Install it directly. Do NOT ask "should I install X?" — just install it.
- If installation fails, TRY EVERY ALTERNATIVE before giving up:
  - Python packages: `pip install`, `pip install --user`, `python -m pip install`
  - System packages on Windows: `winget install`, `choco install`, `scoop install`
  - System packages on Linux: `apt install`, `yum install`, `apk add`
  - If a CLI tool isn't available, use a Python library instead (e.g., `psycopg2` instead of `psql`, `requests` instead of `curl`, `boto3` instead of `aws`)
- NEVER tell the user to "download and install manually." Always find a programmatic way.

## Git Workflow
For multi-step tasks:
- Initialize a git repo if none exists.
- Commit after each logical chunk of work.
- Use descriptive commit messages.
- Use `git diff` to review changes.

## Error Recovery
- If a command fails, read the error carefully and fix the root cause.
- If a tool/package can't be installed, use a Python alternative. You can always `pip install` a library and write a small Python script to accomplish the same thing.
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


def _get_os_context() -> str:
    """Detect the OS and return platform-specific guidance."""
    import platform
    system = platform.system()
    if system == "Windows":
        return (
            "\n\n## Environment: Windows\n"
            "You are running on WINDOWS. Use Windows commands:\n"
            "- Ports: `netstat -an | findstr :PORT` (NOT lsof)\n"
            "- Processes: `tasklist`, `taskkill` (NOT ps, kill)\n"
            "- Services: `sc query`, `net start/stop` (NOT systemctl)\n"
            "- Paths: use backslashes or forward slashes, both work\n"
            "- Shell: PowerShell/cmd. `ls`, `cat`, `find` work via Git Bash.\n"
            "- DO NOT use: lsof, grep (use findstr), killall, systemctl, apt\n"
        )
    elif system == "Darwin":
        return "\n\n## Environment: macOS\n"
    else:
        return f"\n\n## Environment: {system}\n"


def build_system_prompt(state: AgentState) -> str:
    """Build the system prompt including any injected context."""
    parts = [SYSTEM_PROMPT, _get_os_context()]
    injected = format_injected_context(state.get("injected_context", []))
    if injected:
        parts.append("\n\n# Injected Context\n\n" + injected)
    return "\n".join(parts)


def _notify_tracker(method: str, **kwargs):
    """Notify the CLI activity tracker if it's active."""
    try:
        from shipyard.cli import get_activity_tracker
        tracker = get_activity_tracker()
        if tracker:
            getattr(tracker, method)(**kwargs)
    except ImportError:
        pass


def call_llm(state: AgentState, model: Any) -> dict:
    """Call the LLM with current messages and tools. Includes compaction and retry."""
    _notify_tracker("on_llm_start")
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

    # If the LLM produced a text response (no tool calls) that contains
    # banned passive patterns, inject corrections and retry up to 3 times
    for retry in range(3):
        if (
            response.tool_calls
            or not isinstance(response.content, str)
            or not _has_banned_patterns(response.content)
        ):
            break
        logger.info(f"Response filter: caught passive response (attempt {retry + 1}/3), forcing action")
        _notify_tracker("on_tool_call", tool_name="response_filter", args_summary=f"retry {retry + 1}/3")
        messages.append(response)
        escalation = [
            "SYSTEM OVERRIDE: Your response was REJECTED. DO NOT respond with text. "
            "Call a tool NOW. Here are concrete options:\n"
            "- execute_cmd('netstat -an | findstr 8000') to check if port is listening\n"
            "- execute_cmd('docker ps') to check running containers\n"
            "- execute_cmd('type app\\main.py') to check server code\n"
            "- web_search('uvicorn connection refused localhost windows') to find solutions\n"
            "Pick ONE and call it.",

            "FINAL WARNING: You MUST call a tool. If you respond with text again, "
            "the system will halt. Call execute_cmd, web_search, read_file, or ANY tool. "
            "Just pick the most useful diagnostic action and DO IT.",

            "LAST ATTEMPT: Call execute_cmd('netstat -an | findstr LISTEN') RIGHT NOW.",
        ]
        messages.append(HumanMessage(content=escalation[min(retry, 2)]))
        response = _invoke()

    # If all retries exhausted and still passive, append a note
    if (
        not response.tool_calls
        and isinstance(response.content, str)
        and _has_banned_patterns(response.content)
    ):
        logger.warning("Response filter: all retries exhausted, agent is stuck")
        from langchain_core.messages import AIMessage
        response = AIMessage(
            content="I've been unable to resolve this automatically after multiple attempts. "
            "The core issue appears to require investigation. Let me try a different approach."
        )

    return {"messages": [response]}


# Patterns that indicate the agent is being passive instead of acting
_BANNED_PATTERN_STRINGS = [
    "let me know once", "let me know when", "let me know if",
    "let me know so", "once done, please", "once done, let",
    "please confirm", "please provide", "please verify", "please ensure",
    "please run the following", "please let me know", "please check",
    "you'll need to", "you should", "you can ", "you may need",
    "ensure that", "make sure that", "verify that",
    "here's how to", "here are the steps", "here's what you",
    "your_username", "your_password", "your_database",
    "once you've", "once you have", "after you",
    "once your checks", "once this is done",
    "would you like", "if you'd like", "if you have that information",
    "is it possible for you", "cross-verify", "at the infrastructure level",
    "run as administrator", "elevated command prompt",
    "shall i proceed", "shall i continue", "shall i ",
    "do you want me to", "should i proceed", "should i continue",
    "ready to proceed", "want me to",
    "you may want to", "you might want to", "you could try",
    "consider these", "consider the following", "deeper checks",
    "align harmoniously",  # word salad indicator
]


def _has_banned_patterns(text: str) -> bool:
    """Check if text contains patterns indicating passive/suggestion behavior."""
    import re
    lower = text.lower()

    # Any list (numbered or bulleted) with bold items is almost always passive advice
    # Catches: "1. **Foo**:", "- **Foo**:", "* **Foo**:"
    bold_list_items = len(re.findall(r'\n[\d\-\*]+[\.\)]*\s+\*\*', lower))
    if bold_list_items >= 2:
        return True

    matches = sum(1 for p in _BANNED_PATTERN_STRINGS if p in lower)
    # Trigger if 2+ banned patterns found (single occurrence might be okay in context)
    return matches >= 2


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

        # Summarize args for display
        args_summary = ""
        if name in ("read_file", "edit_file", "write_file", "rollback_file"):
            args_summary = args.get("file_path", "")
        elif name == "execute_cmd":
            cmd = args.get("command", "")
            args_summary = cmd[:50] + "..." if len(cmd) > 50 else cmd
        elif name == "search_files":
            args_summary = args.get("pattern", "")
        elif name == "web_search":
            args_summary = args.get("query", "")
        elif name == "web_fetch":
            args_summary = args.get("url", "")[:50]

        _notify_tracker("on_tool_call", tool_name=name, args_summary=args_summary)

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
                result = execute_cmd(
                    command=args["command"],
                    timeout=args.get("timeout", 120),
                    background=args.get("background", False),
                )
            elif name == "check_background":
                result = check_background(pid=args["pid"])
            elif name == "stop_background":
                result = stop_background(pid=args["pid"])
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

        _notify_tracker("on_tool_done", tool_name=name, is_error=result.is_error)

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
    # Only accumulate if ALL tools in this batch failed; reset if any succeeded
    prev_errors = state.get("consecutive_errors", 0)
    total_calls = len(tool_messages)
    all_failed = error_count > 0 and error_count == total_calls
    new_errors = prev_errors + 1 if all_failed else 0

    return {
        "messages": tool_messages,
        "file_read_tracker": tracker.to_dict(),
        "consecutive_errors": new_errors,
    }


MAX_TURNS = 40  # Safety limit to prevent infinite loops


def should_continue(state: AgentState) -> str:
    """Decide whether to continue the agent loop or stop."""
    last_message = state["messages"][-1]

    # Count turns since the last user message (current instruction only)
    turn_count = 0
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            break
        if isinstance(m, AIMessage) and m.tool_calls:
            turn_count += 1

    # Circuit-break on too many turns for this instruction
    if turn_count >= MAX_TURNS:
        logger.warning(f"Turn limit: {turn_count} turns reached, stopping agent")
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "cancel_tools"
        return "end"

    # Circuit-break on repeated failures
    if state.get("consecutive_errors", 0) >= 5:
        logger.warning("Circuit breaker: 5+ consecutive tool errors, stopping agent")
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "cancel_tools"
        return "end"

    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "execute_tools"
    return "end"


def cancel_tools(state: AgentState) -> dict:
    """Inject cancellation ToolMessages for pending tool calls when circuit breaker fires."""
    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        return {"messages": [], "consecutive_errors": 0}

    tool_messages = []
    for tool_call in last_message.tool_calls:
        tool_messages.append(
            ToolMessage(
                content="Cancelled: agent stopped after 5+ consecutive errors. Try a different approach.",
                tool_call_id=tool_call["id"],
                status="error",
            )
        )

    return {"messages": tool_messages, "consecutive_errors": 0}


def _make_error(msg: str):
    from shipyard.tools.base import ToolResult
    return ToolResult(output=msg, is_error=True)
