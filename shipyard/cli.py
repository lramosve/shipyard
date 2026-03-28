"""Interactive CLI for directing the Shipyard agent."""

from __future__ import annotations

import os
import sys
import threading
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

from langchain_core.messages import HumanMessage

from shipyard.agent.graph import build_agent_graph
from shipyard.agent.supervisor import build_supervisor_graph
from shipyard.agent.nodes import reset_snapshot_store
from shipyard.config import settings
from shipyard.context.injection import load_context_from_file
from shipyard.tracing.setup import configure_tracing


class ActivityTracker:
    """Log-style activity display that prints each status on its own line."""

    def __init__(self):
        self._start_time = 0.0
        self._turn = 0
        self._last_printed = ""

    def start(self):
        self._start_time = time.time()
        self._turn = 0
        self._last_printed = ""

    def _log(self, status: str, color: str = "33"):
        elapsed = int(time.time() - self._start_time)
        display = status[:80]
        # Only print if status changed (avoid flooding)
        key = f"{self._turn}:{status}"
        if key != self._last_printed:
            self._last_printed = key
            print(f"\033[{color}m  [{elapsed:>4}s] {display}\033[0m", flush=True)

    def on_llm_start(self):
        self._turn += 1
        self._log(f"Turn {self._turn}: calling LLM")

    def on_tool_call(self, tool_name: str, args_summary: str = ""):
        detail = f": {args_summary}" if args_summary else ""
        self._log(f"Turn {self._turn}: {tool_name}{detail}")

    def on_tool_done(self, tool_name: str, is_error: bool = False):
        color = "31" if is_error else "32"  # red for error, green for success
        status = "ERROR" if is_error else "done"
        self._log(f"Turn {self._turn}: {tool_name} -> {status}", color=color)

    def stop(self):
        elapsed = int(time.time() - self._start_time)
        print(f"\033[32m  [{elapsed:>4}s] Done ({self._turn} turns)\033[0m", flush=True)


# Global tracker accessible from nodes
_activity_tracker: ActivityTracker | None = None


def get_activity_tracker() -> ActivityTracker | None:
    return _activity_tracker


def make_state(working_dir: str | None = None) -> dict:
    return {
        "messages": [],
        "file_read_tracker": {},
        "injected_context": [],
        "working_directory": working_dir or os.path.abspath(settings.working_directory),
        "consecutive_errors": 0,
        "architecture_plan": "",
        "architecture_plan_json": "",
        "current_phase": "architect",
        "review_issues": [],
        "iteration_count": 0,
        "previous_issues": [],
    }


def print_help():
    print("""
Commands:
  /quit, /exit     Exit the CLI
  /reset           Clear session state
  /supervisor <instruction>   Use multi-agent supervisor mode
  /context <filepath>         Inject a file as context
  /history         Show message count
  /help            Show this help

Anything else is sent as an instruction to the agent.
""")


def _cleanup_background_processes():
    """Kill any background processes started during the session."""
    from shipyard.tools.execute_cmd import _background_processes, _kill_process_tree
    for pid, entry in list(_background_processes.items()):
        try:
            _kill_process_tree(entry["proc"])
        except Exception:
            pass
    _background_processes.clear()


def main():
    configure_tracing()

    working_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(".")

    agent = build_agent_graph()
    supervisor = build_supervisor_graph()
    state = make_state(working_dir)
    session = PromptSession(history=InMemoryHistory())

    print(f"Shipyard CLI — model: {settings.llm_model} ({settings.llm_provider})")
    print(f"Working directory: {working_dir}")
    print("Type /help for commands, or enter an instruction.\n")

    while True:
        try:
            instruction = session.prompt("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            _cleanup_background_processes()
            break

        if not instruction:
            continue

        if instruction in ("/quit", "/exit"):
            print("Bye.")
            break

        if instruction == "/reset":
            state = make_state(working_dir)
            reset_snapshot_store()
            print("Session reset.")
            continue

        if instruction == "/help":
            print_help()
            continue

        if instruction == "/history":
            count = len(state["messages"])
            print(f"Messages in session: {count}")
            continue

        if instruction.startswith("/context "):
            filepath = instruction[9:].strip()
            try:
                ctx = load_context_from_file(filepath)
                state["injected_context"].append(ctx)
                print(f"Loaded context from {ctx['source']} ({len(ctx['content'])} chars)")
            except FileNotFoundError as e:
                print(f"Error: {e}")
            continue

        # Determine which graph to use
        use_supervisor = False
        if instruction.startswith("/supervisor "):
            instruction = instruction[12:].strip()
            use_supervisor = True

        state["messages"].append(HumanMessage(content=instruction))

        global _activity_tracker
        tracker = ActivityTracker()
        _activity_tracker = tracker
        tracker.start()
        try:
            graph = supervisor if use_supervisor else agent
            result = graph.invoke(state)
            tracker.stop()
            _activity_tracker = None

            # Update state
            state["messages"] = result.get("messages", state["messages"])
            state["file_read_tracker"] = result.get("file_read_tracker", state["file_read_tracker"])
            state["consecutive_errors"] = result.get("consecutive_errors", 0)
            state["architecture_plan"] = result.get("architecture_plan", state.get("architecture_plan", ""))
            state["architecture_plan_json"] = result.get("architecture_plan_json", state.get("architecture_plan_json", ""))
            state["current_phase"] = result.get("current_phase", "architect")
            state["review_issues"] = result.get("review_issues", [])
            state["iteration_count"] = result.get("iteration_count", 0)
            state["previous_issues"] = result.get("previous_issues", [])

            # Print the last AI response
            for msg in reversed(result.get("messages", [])):
                if msg.type == "ai" and msg.content:
                    content = msg.content
                    # Handle Anthropic's list-of-blocks format
                    if isinstance(content, list):
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block["text"])
                            elif isinstance(block, str):
                                text_parts.append(block)
                        content = "\n".join(text_parts) if text_parts else str(msg.content)
                    print(f"\n{content}\n", flush=True)
                    break
            else:
                print("(Agent produced no response)\n", flush=True)
        except KeyboardInterrupt:
            tracker.stop()
            _activity_tracker = None
            print("\n\nInterrupted. Returning to prompt.\n")
            continue
        except BaseException as e:
            try:
                tracker.stop()
            except Exception:
                pass
            _activity_tracker = None
            # Print error prominently so it's never missed
            import traceback
            print(f"\n\033[31m{'=' * 60}", flush=True)
            print(f"AGENT ERROR: {type(e).__name__}: {e}")
            print(f"{'=' * 60}\033[0m", flush=True)
            traceback.print_exc()
            if isinstance(e, (SystemExit, GeneratorExit)):
                break
            print("Returning to prompt.\n", flush=True)
            continue


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        import traceback
        print(f"\n\033[31mFATAL: {type(e).__name__}: {e}\033[0m", flush=True)
        traceback.print_exc()
