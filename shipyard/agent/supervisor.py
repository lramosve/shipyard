"""Multi-agent supervisor — dispatches tasks to coder and researcher workers."""

from __future__ import annotations

from typing import Any, Literal

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from shipyard.agent.graph import ALL_TOOLS, READ_ONLY_TOOLS, build_agent_graph
from shipyard.agent.state import AgentState
from shipyard.config import settings

SUPERVISOR_PROMPT = """You are a supervisor agent that coordinates two specialized workers:

1. **coder** — Can read, write, and edit files, run commands, and search code. Use for any task that involves changing code.
2. **researcher** — Can only read files and search code (read-only). Use for investigation, code review, gathering context, or answering questions about the codebase.

For each user instruction:
- Decide which worker(s) to delegate to.
- If the task needs both research and code changes, first delegate to the researcher, then use those findings to instruct the coder.
- After receiving worker results, synthesize a final response.

Respond with a JSON object indicating your delegation plan:
{"delegate_to": "coder" | "researcher", "task": "description of what the worker should do"}

When you have the final answer and no more delegation is needed, respond with plain text (no JSON)."""


def build_supervisor_graph(
    provider: str | None = None,
    model_name: str | None = None,
) -> Any:
    """Build the multi-agent supervisor graph.

    Supervisor LLM decides which worker to dispatch to. Workers are full
    agent graphs with different tool sets. Results flow back to supervisor.
    """
    prov = provider or settings.llm_provider
    name = model_name or settings.llm_model

    # Build worker graphs
    coder_graph = build_agent_graph(tool_list=ALL_TOOLS, provider=prov, model_name=name)
    researcher_graph = build_agent_graph(tool_list=READ_ONLY_TOOLS, provider=prov, model_name=name)

    # Supervisor model (no tools bound — it delegates via text)
    if prov == "openai":
        supervisor_model = ChatOpenAI(model=name, api_key=settings.openai_api_key)
    else:
        supervisor_model = ChatAnthropic(model=name, api_key=settings.anthropic_api_key, max_tokens=8192)

    def supervisor_node(state: AgentState) -> dict:
        """Supervisor decides what to do next."""
        messages = list(state["messages"])
        if not messages or not isinstance(messages[0], SystemMessage):
            messages.insert(0, SystemMessage(content=SUPERVISOR_PROMPT))
        else:
            messages[0] = SystemMessage(content=SUPERVISOR_PROMPT)

        response = supervisor_model.invoke(messages)
        return {"messages": [response]}

    def route_supervisor(state: AgentState) -> str:
        """Route based on supervisor's response — delegate or finish."""
        import json as _json

        last = state["messages"][-1]
        if not isinstance(last, AIMessage):
            return "end"

        content = last.content or ""
        # Try to parse as delegation JSON
        try:
            parsed = _json.loads(content)
            if "delegate_to" in parsed:
                return parsed["delegate_to"]
        except (_json.JSONDecodeError, TypeError):
            pass

        # Check for delegation in structured content
        if isinstance(content, str) and '"delegate_to"' in content:
            try:
                import re
                match = re.search(r'\{[^}]*"delegate_to"[^}]*\}', content)
                if match:
                    parsed = _json.loads(match.group())
                    return parsed.get("delegate_to", "end")
            except Exception:
                pass

        return "end"

    def run_coder(state: AgentState) -> dict:
        """Run the coder worker on the delegated task."""
        task = _extract_task(state)
        worker_state = {
            "messages": [HumanMessage(content=task)],
            "file_read_tracker": state.get("file_read_tracker", {}),
            "injected_context": state.get("injected_context", []),
            "working_directory": state.get("working_directory", "."),
        }
        result = coder_graph.invoke(worker_state)

        # Extract the last AI message as the worker's output
        summary = _extract_worker_result(result)
        return {
            "messages": [HumanMessage(content=f"[Coder Worker Result]\n{summary}")],
            "file_read_tracker": result.get("file_read_tracker", state.get("file_read_tracker", {})),
        }

    def run_researcher(state: AgentState) -> dict:
        """Run the researcher worker on the delegated task."""
        task = _extract_task(state)
        worker_state = {
            "messages": [HumanMessage(content=task)],
            "file_read_tracker": state.get("file_read_tracker", {}),
            "injected_context": state.get("injected_context", []),
            "working_directory": state.get("working_directory", "."),
        }
        result = researcher_graph.invoke(worker_state)

        summary = _extract_worker_result(result)
        return {
            "messages": [HumanMessage(content=f"[Researcher Worker Result]\n{summary}")],
        }

    # Build the supervisor graph
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("coder", run_coder)
    graph.add_node("researcher", run_researcher)

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges("supervisor", route_supervisor, {
        "coder": "coder",
        "researcher": "researcher",
        "end": END,
    })
    graph.add_edge("coder", "supervisor")
    graph.add_edge("researcher", "supervisor")

    return graph.compile()


def _extract_task(state: AgentState) -> str:
    """Extract the task description from the supervisor's last delegation."""
    import json as _json

    last = state["messages"][-1]
    content = last.content if isinstance(last.content, str) else str(last.content)
    try:
        parsed = _json.loads(content)
        return parsed.get("task", content)
    except Exception:
        import re
        match = re.search(r'\{[^}]*"task"\s*:\s*"([^"]*)"[^}]*\}', content)
        if match:
            return match.group(1)
    return content


def _extract_worker_result(result: dict) -> str:
    """Extract the final text output from a worker graph result."""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return "(Worker produced no output)"
