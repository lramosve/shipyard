"""Multi-agent supervisor — 4 specialized workers with parallel execution.

Workers:
  Architect  (read-only)  — analyzes original codebase, produces architecture plan
  Coder      (all tools)  — implements code per the architecture plan
  Tester     (all tools)  — writes and runs tests
  Reviewer   (read-only)  — compares output against original, flags issues

Flow:
  architect → coder → tester+reviewer (parallel) → fix loop or done
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from shipyard.agent.graph import ALL_TOOLS, READ_ONLY_TOOLS, build_agent_graph
from shipyard.agent.state import AgentState
from shipyard.config import settings

logger = logging.getLogger(__name__)

MAX_FIX_ITERATIONS = 3

# ---------------------------------------------------------------------------
# Worker role prompts
# ---------------------------------------------------------------------------

ARCHITECT_PROMPT = """\
You are the Architect worker. Your job is to analyze the ORIGINAL codebase and produce a structured architecture plan that another agent will follow to rebuild the application from scratch.

You have READ-ONLY access. Do NOT attempt to modify files.

Start by reading the directory structure, package files, configuration, and key source files of the original codebase. Then produce a plan with ALL of these sections:

## 1. TECH STACK
Languages, frameworks, databases, and versions found in the original. The rebuilt project MUST use the same stack. No language swaps. List exact package names and versions from package.json / requirements.txt / Cargo.toml etc.

## 2. DATABASE SCHEMA
All tables, columns, types, relationships, indexes, constraints, and triggers. Include migration count and any seed data patterns. If the DB uses JSONB or flexible schemas, document that pattern.

## 3. API ENDPOINTS
Every route with HTTP method, path, request/response shapes, authentication requirements, and middleware. Group by resource.

## 4. COMPONENT HIERARCHY
Frontend components, their relationships, shared state management, routing structure. Include key third-party UI libraries (e.g., TipTap, Radix, dnd-kit).

## 5. SECURITY REQUIREMENTS
Authentication mechanism (session, JWT, OAuth, PIV), password hashing algorithm (MUST be bcrypt/argon2, never SHA-256), CORS policy, CSRF protection, rate limiting, input validation patterns, audit logging.

## 6. ACCESSIBILITY REQUIREMENTS
ARIA attributes, semantic HTML patterns, keyboard navigation, screen reader support, color contrast compliance, WCAG level. List specific patterns found in the codebase.

## 7. KEY BUSINESS LOGIC
Non-trivial algorithms, state machines, data transformations, validation rules, real-time features (WebSocket, CRDT, polling). Include the AI/agent features if any exist.

## 8. FILE STRUCTURE
Directory layout the rebuilt project should follow. Mirror the original structure.

## 9. CRITICAL RULES
- The rebuilt app MUST use the SAME programming language and framework as the original.
- All security measures from the original MUST be reproduced.
- All accessibility patterns from the original MUST be reproduced.
- No features may be omitted without explicit justification in the plan.

Be exhaustive — anything you omit WILL be lost in the rebuild."""


CODER_PROMPT = """\
You are the Coder worker. Your job is to implement code based on the Architecture Plan provided in your context.

Follow the architecture plan EXACTLY:
- Use the SAME tech stack specified in the plan. Do NOT substitute frameworks or languages.
- Implement ALL database tables with the exact schema specified.
- Implement ALL API endpoints listed in the plan.
- Reproduce ALL components in the hierarchy.
- Include ALL security measures documented (password hashing, CSRF, rate limiting, audit logging).
- Include ALL accessibility patterns documented (ARIA, semantic HTML, keyboard nav).
- Follow the file structure specified in the plan.

Work systematically: create files in dependency order (config → models → routes → frontend), test as you go.
Commit after each logical chunk of work with descriptive messages."""


TESTER_PROMPT = """\
You are the Tester worker. Your job is to write and run tests that validate the implementation matches the Architecture Plan.

Based on the Architecture Plan in your context:
1. Write unit tests for key business logic functions.
2. Write integration tests for API endpoints — verify request/response shapes match the plan.
3. Write database tests — verify schema matches the plan (table names, column types, relationships).
4. Verify security: test that auth is required where specified, passwords are hashed with bcrypt/argon2.
5. Run ALL tests and report results clearly.
6. If tests fail, report WHAT failed and WHY, but do NOT fix the code — the Coder will handle fixes.

Use the testing framework appropriate for the tech stack (pytest for Python, vitest/jest for JS/TS, etc.)."""


REVIEWER_PROMPT = """\
You are the Reviewer worker. Your job is to compare the rebuilt code against the original codebase and the Architecture Plan, and flag any issues.

You have READ-ONLY access. Do NOT attempt to modify files.

Check for:
1. OMISSIONS: API endpoints, database columns, components, or features in the plan that were NOT implemented.
2. DEVIATIONS: Places where the implementation differs from the plan (wrong types, missing fields, different behavior).
3. SECURITY GAPS: Missing auth checks, weak password hashing, exposed secrets, missing input validation, CORS misconfig, missing CSRF, missing rate limiting.
4. ACCESSIBILITY GAPS: Missing ARIA attributes, non-semantic HTML, missing keyboard handlers, color contrast issues.
5. CODE QUALITY: Obvious bugs, unhandled errors, missing error boundaries, monolithic files that should be split.

Output a structured list of issues. For each issue, use this EXACT format (one per line):

- [CRITICAL] file_path: description of issue
- [MAJOR] file_path: description of issue
- [MINOR] file_path: description of issue

If no issues are found, output exactly: NO ISSUES FOUND"""


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_supervisor_graph(
    provider: str | None = None,
    model_name: str | None = None,
) -> Any:
    """Build the multi-agent supervisor graph with 4 specialized workers.

    Phase flow: architect → coder → tester+reviewer (parallel) → fix or done
    """
    prov = provider or settings.llm_provider
    name = model_name or settings.llm_model

    # Build worker agent graphs
    architect_graph = build_agent_graph(tool_list=READ_ONLY_TOOLS, provider=prov, model_name=name)
    coder_graph = build_agent_graph(tool_list=ALL_TOOLS, provider=prov, model_name=name)
    tester_graph = build_agent_graph(tool_list=ALL_TOOLS, provider=prov, model_name=name)
    reviewer_graph = build_agent_graph(tool_list=READ_ONLY_TOOLS, provider=prov, model_name=name)

    # -- Node functions ------------------------------------------------

    def supervisor_node(state: AgentState) -> dict:
        """Initialize phase on first entry. Pure routing logic — no LLM."""
        if not state.get("current_phase"):
            return {"current_phase": "architect"}
        return {}

    def route_supervisor(state: AgentState) -> str | list[Send]:
        """Deterministic routing based on current_phase."""
        phase = state.get("current_phase", "architect")

        if phase == "architect":
            return "architect"
        if phase in ("coder", "fix"):
            return "coder"
        if phase == "test_review":
            return [Send("tester", state), Send("reviewer", state)]
        # phase == "done" or unknown
        return END

    def run_architect(state: AgentState) -> dict:
        """Run the Architect worker to produce an architecture plan."""
        user_instruction = _get_original_instruction(state)
        task = (
            f"{ARCHITECT_PROMPT}\n\n"
            f"## User Instruction\n{user_instruction}\n\n"
            f"Working directory: {state.get('working_directory', '.')}"
        )

        worker_state = _make_worker_state(state, task)
        result = architect_graph.invoke(worker_state)

        plan_text = _extract_worker_result(result)
        logger.info(f"Architect produced plan ({len(plan_text)} chars)")

        # Store as persistent injected context
        plan_context = {
            "type": "architecture_plan",
            "source": "architect_worker",
            "content": plan_text,
        }
        updated_context = list(state.get("injected_context", []))
        # Remove any prior architecture plan context
        updated_context = [c for c in updated_context if c.get("type") != "architecture_plan"]
        updated_context.append(plan_context)

        return {
            "messages": [HumanMessage(content=f"[Architect Worker Result]\n{plan_text}")],
            "architecture_plan": plan_text,
            "injected_context": updated_context,
            "current_phase": "coder",
            "file_read_tracker": result.get("file_read_tracker", state.get("file_read_tracker", {})),
        }

    def run_coder(state: AgentState) -> dict:
        """Run the Coder worker to implement or fix code."""
        review_issues = state.get("review_issues", [])
        plan = state.get("architecture_plan", "")
        user_instruction = _get_original_instruction(state)

        if review_issues:
            # Fix cycle: address reviewer issues
            issues_text = "\n".join(f"  {issue}" for issue in review_issues)
            task = (
                f"{CODER_PROMPT}\n\n"
                f"## FIX CYCLE — Address these issues found by the Reviewer:\n{issues_text}\n\n"
                f"Refer to the Architecture Plan in your context for correct behavior."
            )
        else:
            # Initial coding
            task = (
                f"{CODER_PROMPT}\n\n"
                f"## User Instruction\n{user_instruction}\n\n"
                f"Working directory: {state.get('working_directory', '.')}"
            )

        worker_state = _make_worker_state(state, task)
        result = coder_graph.invoke(worker_state)

        summary = _extract_worker_result(result)
        logger.info(f"Coder completed ({'fix cycle' if review_issues else 'initial build'})")

        return {
            "messages": [HumanMessage(content=f"[Coder Worker Result]\n{summary}")],
            "current_phase": "test_review",
            "review_issues": [],  # clear issues after fix attempt
            "file_read_tracker": result.get("file_read_tracker", state.get("file_read_tracker", {})),
        }

    def run_tester(state: AgentState) -> dict:
        """Run the Tester worker to write and execute tests."""
        plan = state.get("architecture_plan", "")
        task = (
            f"{TESTER_PROMPT}\n\n"
            f"Working directory: {state.get('working_directory', '.')}"
        )

        worker_state = _make_worker_state(state, task)
        result = tester_graph.invoke(worker_state)

        summary = _extract_worker_result(result)
        logger.info("Tester completed")

        return {
            "messages": [HumanMessage(content=f"[Tester Worker Result]\n{summary}")],
            "file_read_tracker": result.get("file_read_tracker", state.get("file_read_tracker", {})),
        }

    def run_reviewer(state: AgentState) -> dict:
        """Run the Reviewer worker to compare output against original."""
        user_instruction = _get_original_instruction(state)
        task = (
            f"{REVIEWER_PROMPT}\n\n"
            f"## User Instruction\n{user_instruction}\n\n"
            f"Working directory: {state.get('working_directory', '.')}"
        )

        worker_state = _make_worker_state(state, task)
        result = reviewer_graph.invoke(worker_state)

        summary = _extract_worker_result(result)
        issues = _parse_review_issues(summary)
        logger.info(f"Reviewer completed — {len(issues)} issues found")

        return {
            "messages": [HumanMessage(content=f"[Reviewer Worker Result]\n{summary}")],
            "review_issues": issues,
        }

    def merge_results(state: AgentState) -> dict:
        """Merge tester + reviewer results. Decide whether to iterate."""
        issues = state.get("review_issues", [])
        iteration = state.get("iteration_count", 0)

        if issues and iteration < MAX_FIX_ITERATIONS:
            logger.info(f"Review found {len(issues)} issues, dispatching fix cycle {iteration + 1}/{MAX_FIX_ITERATIONS}")
            return {
                "current_phase": "fix",
                "iteration_count": iteration + 1,
            }
        else:
            if issues:
                logger.warning(f"Review still has {len(issues)} issues after {iteration} fix cycles, finishing")
            else:
                logger.info("Review passed — no issues found")
            return {
                "current_phase": "done",
            }

    # -- Wire the graph ------------------------------------------------

    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("architect", run_architect)
    graph.add_node("coder", run_coder)
    graph.add_node("tester", run_tester)
    graph.add_node("reviewer", run_reviewer)
    graph.add_node("merge_results", merge_results)

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges("supervisor", route_supervisor, ["architect", "coder", "tester", "reviewer", END])
    graph.add_edge("architect", "supervisor")
    graph.add_edge("coder", "supervisor")
    graph.add_edge("tester", "merge_results")
    graph.add_edge("reviewer", "merge_results")
    graph.add_edge("merge_results", "supervisor")

    return graph.compile()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_worker_state(state: AgentState, task: str) -> dict:
    """Create a fresh worker state with the task and inherited context."""
    return {
        "messages": [HumanMessage(content=task)],
        "file_read_tracker": state.get("file_read_tracker", {}),
        "injected_context": state.get("injected_context", []),
        "working_directory": state.get("working_directory", "."),
        "consecutive_errors": 0,
    }


def _get_original_instruction(state: AgentState) -> str:
    """Extract the original user instruction from messages."""
    for msg in state.get("messages", []):
        if isinstance(msg, HumanMessage) and not msg.content.startswith("["):
            return msg.content
    return "(no instruction found)"


def _extract_worker_result(result: dict) -> str:
    """Extract the final text output from a worker graph result."""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return "(Worker produced no output)"


def _parse_review_issues(text: str) -> list[str]:
    """Parse structured issue lines from the reviewer's output.

    Expected format: - [CRITICAL|MAJOR|MINOR] file_path: description
    Falls back to treating non-empty lines with severity markers as issues.
    """
    if "NO ISSUES FOUND" in text.upper():
        return []

    issues = []
    for line in text.split("\n"):
        line = line.strip()
        if re.match(r"^-\s*\[(CRITICAL|MAJOR|MINOR)\]", line, re.IGNORECASE):
            # Remove the leading "- " and keep the rest
            issues.append(line.lstrip("- ").strip())
        elif re.match(r"^\*\s*\[(CRITICAL|MAJOR|MINOR)\]", line, re.IGNORECASE):
            issues.append(line.lstrip("* ").strip())
    return issues
