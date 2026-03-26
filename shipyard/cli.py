"""Interactive CLI for directing the Shipyard agent."""

from __future__ import annotations

import itertools
import os
import sys
import threading
import time

from langchain_core.messages import HumanMessage

from shipyard.agent.graph import build_agent_graph
from shipyard.agent.supervisor import build_supervisor_graph
from shipyard.agent.nodes import reset_snapshot_store
from shipyard.config import settings
from shipyard.context.injection import load_context_from_file
from shipyard.tracing.setup import configure_tracing


class ActivityTracker:
    """Live activity display with continuously updating timer."""

    def __init__(self):
        self._start_time = 0.0
        self._turn = 0
        self._last_status = ""
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._start_time = time.time()
        self._turn = 0
        self._last_status = "Starting..."
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()

    def _tick(self):
        """Background thread that refreshes the display every second."""
        while not self._stop_event.is_set():
            self._render()
            self._stop_event.wait(1.0)

    def _render(self):
        elapsed = int(time.time() - self._start_time)
        display = self._last_status[:70]
        print(f"\r\033[33m  [{elapsed:>4}s] {display}\033[0m" + " " * 10, end="", flush=True)

    def on_llm_start(self):
        self._turn += 1
        self._last_status = f"Turn {self._turn}: calling LLM"

    def on_tool_call(self, tool_name: str, args_summary: str = ""):
        detail = f": {args_summary}" if args_summary else ""
        self._last_status = f"Turn {self._turn}: {tool_name}{detail}"

    def on_tool_done(self, tool_name: str, is_error: bool = False):
        status = "ERROR" if is_error else "done"
        self._last_status = f"Turn {self._turn}: {tool_name} -> {status}"

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        elapsed = int(time.time() - self._start_time)
        print(f"\r\033[32m  [{elapsed:>4}s] Done ({self._turn} turns)\033[0m" + " " * 20)


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


def main():
    configure_tracing()

    working_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(".")

    agent = build_agent_graph()
    supervisor = build_supervisor_graph()
    state = make_state(working_dir)

    print(f"Shipyard CLI — model: {settings.llm_model} ({settings.llm_provider})")
    print(f"Working directory: {working_dir}")
    print("Type /help for commands, or enter an instruction.\n")

    while True:
        try:
            instruction = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
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

            # Print the last AI response
            for msg in reversed(result.get("messages", [])):
                if msg.type == "ai" and msg.content:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    print(f"\n{content}\n")
                    break
        except KeyboardInterrupt:
            tracker.stop()
            _activity_tracker = None
            print("\n\nInterrupted. Returning to prompt.\n")
            continue
        except Exception as e:
            tracker.stop()
            _activity_tracker = None
            print(f"\nError: {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    main()
