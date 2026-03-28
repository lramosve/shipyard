"""Multi-agent supervisor — 4 specialized workers with parallel execution.

Workers:
  Architect  (read-only)  — analyzes original codebase, produces structured architecture plan
  Coder      (all tools)  — implements code per the architecture plan checklist
  Tester     (all tools)  — writes and runs tests
  Reviewer   (read-only)  — compares output against original, flags issues

Flow:
  architect → coder → tester+reviewer (parallel) → fix loop or done

Three-phase approach:
  PLAN (architect) → EXECUTE (coder) → REVIEW (tester + reviewer)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from shipyard.agent.graph import ALL_TOOLS, READ_ONLY_TOOLS, build_agent_graph
from shipyard.agent.plan_schema import (
    PLAN_JSON_SCHEMA,
    ArchitecturePlan,
    parse_plan_from_text,
)
from shipyard.agent.state import AgentState
from shipyard.config import settings

logger = logging.getLogger(__name__)

MAX_FIX_ITERATIONS = 3
MAX_FILE_LINES = 300  # files exceeding this trigger a MAJOR review issue

# ---------------------------------------------------------------------------
# Worker role prompts
# ---------------------------------------------------------------------------

ARCHITECT_PROMPT = """\
You are the Architect worker. Your job is to analyze the ORIGINAL codebase and produce a STRUCTURED architecture plan (JSON) that another agent will follow to rebuild the application.

You have READ-ONLY access. Do NOT attempt to modify files.

Start by reading the directory structure, package files, configuration, database schema, and key source files of the original codebase. Be EXHAUSTIVE — anything you omit WILL be lost in the rebuild.

Then produce the plan as a JSON code block matching this schema:

```
{schema}
```

### Section guidance:

**tech_stack**: List EVERY dependency from package.json / requirements.txt / Cargo.toml etc. Include exact version constraints. The rebuilt project MUST use the same stack. NEVER swap languages.

**database_tables**: Document ALL tables, columns, types, relationships, indexes, constraints, triggers. Include migration count and JSONB/flexible schema patterns. If the database is PostgreSQL, document PostgreSQL-specific features used (JSONB operators, GIN indexes, CTEs, etc.).

**api_endpoints**: Document EVERY route with method, path, request/response shapes, auth requirements, middleware. Group by resource. Include health checks, error response formats, and WebSocket endpoints.

**components**: Document the frontend component tree. One component per file. Include state management pattern (Context, Redux, TanStack Query, etc.), routing structure, and key third-party UI libraries (TipTap, Radix, dnd-kit, etc.).

**file_structure**: Define the EXACT directory layout the rebuilt project should follow. Mirror the original. Set max_lines to 300 for each file — no monoliths.

**security_requirements**: Document ALL security measures: authentication mechanism (session/JWT/OAuth/PIV), password hashing (MUST be bcrypt/argon2), CORS, CSRF, rate limiting, input validation, audit logging, security headers (helmet).

**business_rules**: Document non-trivial algorithms, state machines, workflows, validation rules, real-time features (WebSocket, CRDT, polling), AI/agent features if any.

**accessibility_notes**: ARIA patterns, semantic HTML, keyboard navigation, WCAG level, color contrast, screen reader support.

**critical_rules**: Hard constraints: same language, all security reproduced, all accessibility reproduced, no feature omissions without justification.

Output your plan as a single JSON code block (```json ... ```) matching the schema above. Do NOT output free-form text outside the JSON block."""

CODER_PROMPT = """\
You are the Coder worker. Your job is to implement code based on the Architecture Plan checklist provided in your context.

## Mandatory workflow:
1. Read the Architecture Plan Checklist in your context FIRST.
2. Create the directory structure from the File Structure section BEFORE writing any implementation code.
3. Work through the checklist section by section, in order.
4. After each file, verify it doesn't exceed 300 lines. If it does, split it immediately.
5. After each section, run a type-check or syntax check (tsc --noEmit, python -m py_compile, etc.).

## Rules:
- Use the EXACT tech stack from the plan. NEVER substitute frameworks or languages.
- Implement ALL database tables with the exact schema specified. Use real database drivers (pg, sqlite3, etc.), NOT in-memory arrays.
- Implement ALL API endpoints listed. Every single one.
- Create ALL components listed. One component per file, max 300 lines each.
- Include ALL security measures: auth middleware, password hashing (bcrypt/argon2), CSRF, rate limiting, input validation, audit logging, security headers.
- Include ALL accessibility patterns: semantic HTML, ARIA labels, keyboard navigation.
- Follow the file structure EXACTLY as specified in the plan.

## File organization:
- NEVER put all frontend code in a single file.
- NEVER put all API routes in a single file.
- NEVER put all types/models in a single file.
- Each file should have a single responsibility.
- If a file approaches 300 lines, split it before moving on.

## When in a FIX cycle:
- Address EVERY issue listed. Do not skip any.
- After fixing, verify the fix compiles/passes.
- Do not introduce new issues while fixing old ones."""

TESTER_PROMPT = """\
You are the Tester worker. Your job is to write and run tests that validate the implementation matches the Architecture Plan.

Based on the Architecture Plan in your context:
1. Write unit tests for ALL key business logic functions.
2. Write integration tests for ALL API endpoints — verify request/response shapes match the plan.
3. Write database tests — verify schema matches the plan (table names, column types, relationships, seed data).
4. Verify security: test that auth is required where specified, passwords are hashed with bcrypt/argon2 (never plaintext or SHA-256).
5. Test error paths: invalid input, missing auth, 404s, constraint violations.
6. Run ALL tests and report results clearly.

Report test failures in this format so they can be automatically parsed:
- [CRITICAL] file_path: test_name — FAILED: description of failure
- [MAJOR] file_path: test_name — FAILED: description of failure

If all tests pass, output: ALL TESTS PASSED

Use the testing framework appropriate for the tech stack (pytest for Python, vitest/jest for JS/TS, etc.)."""

REVIEWER_PROMPT = """\
You are the Reviewer worker. Your job is to compare the rebuilt code against the original codebase and the Architecture Plan, and flag any issues.

You have READ-ONLY access. Do NOT attempt to modify files.

## Review checklist (check EVERY item):

### Completeness
- Are ALL API endpoints from the plan implemented?
- Are ALL database tables from the plan created with correct columns?
- Are ALL frontend components from the plan created?
- Are ALL business rules implemented?
- Is the file structure correct?

### Security
- Is authentication implemented? (sessions, JWT, or as specified)
- Are passwords hashed with bcrypt/argon2? (NEVER SHA-256 or md5)
- Is CSRF protection present? (for session-based auth)
- Is rate limiting configured? (especially on login)
- Is input validation present on all endpoints? (Zod, Joi, Pydantic, etc.)
- Are security headers set? (helmet, CORS)
- Is audit logging implemented?

### Accessibility
- Are semantic HTML elements used? (nav, main, article, section)
- Are ARIA labels present on interactive elements?
- Is keyboard navigation implemented?

### Code Quality
- Does any file exceed 300 lines? (flag as MAJOR)
- Are there monolithic files that combine unrelated concerns?
- Is error handling present (error boundaries, try-catch, middleware)?
- Are there obvious bugs or unhandled edge cases?

### Fidelity to Original
- Does the rebuild use the SAME language and framework?
- Does the data model match the original's pattern? (unified vs normalized)
- Are the same third-party libraries used where they matter?

Output a structured list of issues. For each issue, use this EXACT format (one per line):

- [CRITICAL] file_path: description of issue
- [MAJOR] file_path: description of issue
- [MINOR] file_path: description of issue

CRITICAL = missing security, wrong language/framework, data loss risk
MAJOR = missing feature, monolithic file, missing tests, accessibility gap
MINOR = style issue, naming inconsistency, missing documentation

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
        """Run the Architect worker to produce a structured architecture plan."""
        user_instruction = _get_original_instruction(state)
        prompt = ARCHITECT_PROMPT.format(schema=PLAN_JSON_SCHEMA)
        task = (
            f"{prompt}\n\n"
            f"## User Instruction\n{user_instruction}\n\n"
            f"Working directory: {state.get('working_directory', '.')}"
        )

        worker_state = _make_worker_state(state, task)
        result = architect_graph.invoke(worker_state)

        plan_text = _extract_worker_result(result)
        logger.info(f"Architect produced plan ({len(plan_text)} chars)")

        # Try to parse structured plan
        parsed = parse_plan_from_text(plan_text)
        plan_json = ""
        checklist = ""
        if parsed:
            plan_json = parsed.model_dump_json(indent=2)
            checklist = parsed.to_checklist()
            logger.info(f"Structured plan parsed: {len(parsed.api_endpoints)} endpoints, "
                        f"{len(parsed.database_tables)} tables, {len(parsed.components)} components, "
                        f"{len(parsed.file_structure)} files")
            warnings = parsed.validate_completeness()
            for w in warnings:
                logger.warning(f"Plan incomplete: {w}")
        else:
            logger.warning("Could not parse structured plan — falling back to raw text")

        # Store as persistent injected context (use checklist if available, else raw text)
        plan_content = checklist if checklist else plan_text
        plan_context = {
            "type": "architecture_plan",
            "source": "architect_worker",
            "content": plan_content,
        }
        updated_context = list(state.get("injected_context", []))
        updated_context = [c for c in updated_context if c.get("type") != "architecture_plan"]
        updated_context.append(plan_context)

        return {
            "messages": [HumanMessage(content=f"[Architect Worker Result]\n{plan_text}")],
            "architecture_plan": plan_text,
            "architecture_plan_json": plan_json,
            "injected_context": updated_context,
            "current_phase": "coder",
            "file_read_tracker": result.get("file_read_tracker", state.get("file_read_tracker", {})),
        }

    def run_coder(state: AgentState) -> dict:
        """Run the Coder worker to implement or fix code."""
        review_issues = state.get("review_issues", [])
        user_instruction = _get_original_instruction(state)

        # Build the checklist from the structured plan if available
        checklist_section = ""
        plan_json_str = state.get("architecture_plan_json", "")
        if plan_json_str:
            try:
                plan_obj = ArchitecturePlan.model_validate_json(plan_json_str)
                checklist_section = f"\n\n{plan_obj.to_checklist()}"
            except Exception:
                pass

        if review_issues:
            # Fix cycle: address reviewer/tester issues
            issues_text = "\n".join(f"  - {issue}" for issue in review_issues)
            task = (
                f"{CODER_PROMPT}{checklist_section}\n\n"
                f"## FIX CYCLE — Address ALL of these issues:\n{issues_text}\n\n"
                f"Fix every issue listed above. Do not skip any."
            )
        else:
            # Initial coding
            task = (
                f"{CODER_PROMPT}{checklist_section}\n\n"
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
        task = (
            f"{TESTER_PROMPT}\n\n"
            f"Working directory: {state.get('working_directory', '.')}"
        )

        worker_state = _make_worker_state(state, task)
        result = tester_graph.invoke(worker_state)

        summary = _extract_worker_result(result)
        logger.info("Tester completed")

        # Extract test failure issues in the same format as reviewer
        test_issues = _parse_review_issues(summary)
        logger.info(f"Tester found {len(test_issues)} failing tests")

        return {
            "messages": [HumanMessage(content=f"[Tester Worker Result]\n{summary}")],
            "review_issues": test_issues,
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

        # Add file-size enforcement issues
        file_issues = _check_file_sizes(state.get("working_directory", "."))
        issues.extend(file_issues)

        logger.info(f"Reviewer completed — {len(issues)} issues found ({len(file_issues)} file-size)")

        return {
            "messages": [HumanMessage(content=f"[Reviewer Worker Result]\n{summary}")],
            "review_issues": issues,
        }

    def merge_results(state: AgentState) -> dict:
        """Merge tester + reviewer results. Decide whether to iterate."""
        all_issues = state.get("review_issues", [])
        iteration = state.get("iteration_count", 0)
        previous = state.get("previous_issues", [])

        # Deduplicate issues
        unique_issues = list(dict.fromkeys(all_issues))

        # Filter: only CRITICAL and MAJOR trigger fix cycles
        actionable = [i for i in unique_issues if _is_actionable(i)]
        minor_only = [i for i in unique_issues if not _is_actionable(i)]

        if minor_only:
            logger.info(f"Skipping {len(minor_only)} MINOR issues (non-blocking)")

        # Detect no-progress: same issues as last cycle
        if actionable and set(actionable) == set(previous):
            logger.warning(f"No progress in fix cycle {iteration} — same {len(actionable)} issues remain. Stopping.")
            return {
                "current_phase": "done",
                "review_issues": unique_issues,
            }

        if actionable and iteration < MAX_FIX_ITERATIONS:
            logger.info(f"Review found {len(actionable)} actionable issues, dispatching fix cycle {iteration + 1}/{MAX_FIX_ITERATIONS}")
            return {
                "current_phase": "fix",
                "iteration_count": iteration + 1,
                "review_issues": actionable,
                "previous_issues": list(actionable),
            }
        else:
            if actionable:
                logger.warning(f"Review still has {len(actionable)} actionable issues after {iteration} fix cycles, finishing")
            else:
                logger.info("Review passed — no actionable issues found")
            return {
                "current_phase": "done",
                "review_issues": unique_issues,
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
    """Parse structured issue lines from reviewer/tester output.

    Expected format: - [CRITICAL|MAJOR|MINOR] file_path: description
    """
    if "NO ISSUES FOUND" in text.upper() or "ALL TESTS PASSED" in text.upper():
        return []

    issues = []
    for line in text.split("\n"):
        line = line.strip()
        if re.match(r"^[-*]\s*\[(CRITICAL|MAJOR|MINOR)\]", line, re.IGNORECASE):
            # Remove the leading "- " or "* " and keep the rest
            cleaned = re.sub(r"^[-*]\s*", "", line).strip()
            issues.append(cleaned)
    return issues


def _is_actionable(issue: str) -> bool:
    """Check if an issue is CRITICAL or MAJOR (triggers fix cycle)."""
    upper = issue.upper()
    return upper.startswith("[CRITICAL]") or upper.startswith("[MAJOR]")


def _check_file_sizes(working_dir: str) -> list[str]:
    """Check for files exceeding MAX_FILE_LINES in the working directory.

    Returns MAJOR review issues for any file that's too large.
    Skips: node_modules, .git, dist, build, __pycache__, lock files, .min. files.
    """
    import os

    issues = []
    skip_dirs = {"node_modules", ".git", "dist", "build", "__pycache__", ".next", "venv", ".venv"}
    skip_extensions = {".lock", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot"}
    code_extensions = {".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".go", ".java", ".rb", ".vue", ".svelte"}

    try:
        for root, dirs, files in os.walk(working_dir):
            # Skip irrelevant directories
            dirs[:] = [d for d in dirs if d not in skip_dirs]

            for fname in files:
                _, ext = os.path.splitext(fname)
                if ext not in code_extensions:
                    continue
                if ".min." in fname:
                    continue

                filepath = os.path.join(root, fname)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        line_count = sum(1 for _ in f)
                    if line_count > MAX_FILE_LINES:
                        rel_path = os.path.relpath(filepath, working_dir)
                        issues.append(
                            f"[MAJOR] {rel_path}: File has {line_count} lines "
                            f"(max {MAX_FILE_LINES}). Split into smaller modules."
                        )
                except OSError:
                    pass
    except OSError:
        pass

    return issues
