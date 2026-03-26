"""Core agent loop — LangGraph StateGraph wiring."""

from __future__ import annotations

from functools import partial

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool as lc_tool
from langgraph.graph import END, StateGraph

from shipyard.agent.nodes import call_llm, cancel_tools, execute_tools, should_continue
from shipyard.agent.state import AgentState
from shipyard.config import settings


# ---------------------------------------------------------------------------
# LangChain tool definitions (schema only — execution is in nodes.py)
# ---------------------------------------------------------------------------

@lc_tool
def read_file(file_path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """Read a file's contents. Optionally specify a 1-indexed line range."""
    return ""  # Placeholder — actual execution is in execute_tools


@lc_tool
def edit_file(file_path: str, old_string: str, new_string: str) -> str:
    """Make a surgical edit: replace old_string with new_string. old_string must appear exactly once."""
    return ""


@lc_tool
def write_file(file_path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    return ""


@lc_tool
def execute_cmd(command: str, timeout: int = 120, background: bool = False) -> str:
    """Execute a shell command. Use background=True for servers/long-running processes. Use timeout=300 for builds."""
    return ""


@lc_tool
def check_background(pid: int) -> str:
    """Check the status and output of a background process by its PID."""
    return ""


@lc_tool
def stop_background(pid: int) -> str:
    """Stop a background process by its PID."""
    return ""


@lc_tool
def search_files(pattern: str, directory: str = ".", file_glob: str = "*") -> str:
    """Search for a regex pattern across files in a directory."""
    return ""


@lc_tool
def list_files(directory: str = ".", pattern: str | None = None, recursive: bool = False) -> str:
    """List files in a directory, optionally filtered by glob pattern."""
    return ""


@lc_tool
def rollback_file(file_path: str, version: int = -1) -> str:
    """Restore a file to a previous snapshot version. Use version=-1 for most recent pre-edit state."""
    return ""


@lc_tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for documentation, APIs, error solutions, or best practices."""
    return ""


@lc_tool
def web_fetch(url: str, extract_text: bool = True) -> str:
    """Fetch a URL and return its contents. Set extract_text=True to strip HTML and return plain text."""
    return ""


ALL_TOOLS = [read_file, edit_file, write_file, execute_cmd, check_background, stop_background, search_files, list_files, rollback_file, web_search, web_fetch]
READ_ONLY_TOOLS = [read_file, search_files, list_files, web_search, web_fetch]


def _get_model(tool_list=None, provider: str | None = None, model_name: str | None = None):
    """Create the LLM model with tools bound."""
    prov = provider or settings.llm_provider
    name = model_name or settings.llm_model
    tools = tool_list or ALL_TOOLS

    if prov == "openai":
        model = ChatOpenAI(model=name, api_key=settings.openai_api_key)
    else:
        model = ChatAnthropic(model=name, api_key=settings.anthropic_api_key, max_tokens=8192)

    return model.bind_tools(tools)


def build_agent_graph(
    tool_list=None,
    provider: str | None = None,
    model_name: str | None = None,
) -> StateGraph:
    """Build and compile the core agent StateGraph.

    Args:
        tool_list: Override the default tool set (e.g., read-only for researcher).
        provider: "anthropic" or "openai".
        model_name: Model name override.
    """
    model = _get_model(tool_list=tool_list, provider=provider, model_name=model_name)

    graph = StateGraph(AgentState)

    # Nodes
    graph.add_node("call_llm", partial(call_llm, model=model))
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("cancel_tools", cancel_tools)

    # Edges
    graph.set_entry_point("call_llm")
    graph.add_conditional_edges("call_llm", should_continue, {
        "execute_tools": "execute_tools",
        "cancel_tools": "cancel_tools",
        "end": END,
    })
    graph.add_edge("execute_tools", "call_llm")
    graph.add_edge("cancel_tools", END)

    return graph.compile()
