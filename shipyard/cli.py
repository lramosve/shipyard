"""Interactive CLI for directing the Shipyard agent."""

from __future__ import annotations

import os
import sys

from langchain_core.messages import HumanMessage

from shipyard.agent.graph import build_agent_graph
from shipyard.agent.supervisor import build_supervisor_graph
from shipyard.agent.nodes import reset_snapshot_store
from shipyard.config import settings
from shipyard.context.injection import load_context_from_file
from shipyard.tracing.setup import configure_tracing


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

        try:
            graph = supervisor if use_supervisor else agent
            result = graph.invoke(state)

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
        except Exception as e:
            print(f"\nError: {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    main()
