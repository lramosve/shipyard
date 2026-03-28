# CODEAGENT.md — Shipyard

## Agent Architecture (MVP)

### Overview

Shipyard is a LangGraph-based autonomous coding agent with a FastAPI persistent server. The architecture follows a **single-threaded agent loop** with **supervisor-dispatched multi-agent coordination**. The agent was developed over 46 commits from 2026-03-23 to 2026-03-26, totaling ~2,830 lines of Python across 33 source files.

### Agent Loop Design

```
User Instruction (HTTP POST /instruction)
    │
    ▼
┌─────────────────────────────────────────────────┐
│  FastAPI Server (persistent — never restarts)    │
│  Session state: messages, file_read_tracker,     │
│  injected_context, working_directory             │
│  Persistence: SQLite via SessionStore            │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  LangGraph StateGraph                           │
│                                                 │
│  ┌──────────┐    ┌───────────────┐              │
│  │ call_llm │───▶│should_continue│              │
│  └──────────┘    └───────┬───────┘              │
│       ▲                  │                      │
│       │          tool_use│    end_turn           │
│       │                  ▼          │            │
│  ┌────┴──────────┐                  ▼            │
│  │execute_tools  │           Return Response     │
│  └───────────────┘                               │
│                                                 │
│  Safety: MAX_TURNS=40, circuit breaker at 5     │
│  consecutive error batches, banned cmd filter    │
└─────────────────────────────────────────────────┘
```

### State Management

State is defined as a LangGraph `TypedDict` with:
- `messages` — full conversation history (LangGraph `add_messages` reducer handles appending)
- `file_read_tracker` — dict mapping file paths to mtime at read time (for edit validation)
- `injected_context` — list of context items (type, source, content) injected at runtime
- `working_directory` — the root directory the agent operates in
- `consecutive_errors` — circuit breaker counter (resets when any tool succeeds)

State persists across instructions within a session via SQLite (`SessionStore`). New instructions append to existing message history — the agent maintains context from prior turns. Session restoration loads messages, file tracker, and injected context from the database on startup.

### Tool Calls

Tools are defined as LangChain `@tool`-decorated functions for schema generation, but executed by a custom `execute_tools` node that calls the actual tool implementations with session state (e.g., the `FileReadTracker`, `FileSnapshotStore`). This separation keeps tool schemas clean while allowing stateful execution.

Eleven tools are available:
1. `read_file` — read file contents with optional line range, tracks mtime
2. `edit_file` — anchor-based surgical replacement with triple validation
3. `write_file` — create new files or full overwrite (read-before-write enforced)
4. `execute_cmd` — run shell commands with timeout, auto-background detection for servers
5. `check_background` — check status and output of a background process by PID
6. `stop_background` — stop a background process by PID (process tree kill)
7. `search_files` — regex search across files with glob filtering (max 50 results)
8. `list_files` — list directory contents with glob pattern support
9. `rollback_file` — restore a file to a previous snapshot version
10. `web_search` — DuckDuckGo search for documentation and error solutions
11. `web_fetch` — fetch a URL and extract text content (HTML-to-text parser)

### Entry and Exit Conditions

**Normal run:** User submits instruction → agent loops (call_llm → execute_tools → call_llm → ...) until the LLM produces a text-only response (no tool calls) → returns final response.

**Safety limits:**
- Maximum 40 turns per instruction (prevents infinite loops)
- Circuit breaker: 5 consecutive batches where ALL tools fail → `cancel_tools` node injects cancellation messages
- Banned command filter: blocks `rm -rf /`, `mkfs`, fork bombs, `docker exec -it` (interactive flags)

**Error branches:**
- Tool execution error → error returned as `ToolMessage` with `status="error"` → LLM sees error and can self-correct
- File not read before edit → immediate error returned, LLM learns to read first
- Stale file (modified externally since last read) → error returned, LLM re-reads
- Multiple matches for `old_string` → error with match count and line numbers of each match
- Command timeout → error with partial output, LLM can retry or adjust
- Passive response detected → response filter escalates with SYSTEM OVERRIDE (up to 3 retries)

### LLM Provider Abstraction

The LLM is accessed through a Python `Protocol` class (`LLMProvider`) that both `AnthropicProvider` and `OpenAIProvider` implement. For the LangGraph integration, `langchain-anthropic`'s `ChatAnthropic` and `langchain-openai`'s `ChatOpenAI` are used directly (native LangGraph tool binding). Changing `LLM_PROVIDER=openai` and `LLM_MODEL=gpt-5.4` in `.env` switches the underlying model with no code changes. Both providers support 120s request timeout and exponential backoff retry (base 1s, rate limit 30s).

### Tracing

LangSmith tracing is enabled via environment variables (`LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT=shipyard`). Every LLM call, tool execution, and state transition is automatically traced. Traces are viewable as shareable links in the LangSmith dashboard, providing full visibility into agent decision-making, token usage, and error recovery paths.

### Context Compaction

When conversation history approaches 50% of the 200K context window (~4 chars/token heuristic), the `compact_messages` function:
1. Preserves the system prompt and last 10 messages
2. Sends older messages to the LLM for summarization
3. Sanitizes the boundary to avoid orphaned `ToolMessage` nodes (fix from commit `13229e5`)
4. Replaces old messages with a compact `SystemMessage` summary

### Passive Response Filter

The agent detects when the LLM produces passive language ("let me know," "please confirm," "you should") and escalates with system overrides forcing tool calls. The escalation ladder:
1. **Retry 1:** Helpful hint — "You are autonomous. Take action now."
2. **Retry 2:** Final warning — "SYSTEM OVERRIDE: You MUST call a tool."
3. **Retry 3:** Direct command — forces a specific tool call suggestion

This prevents the LLM from suggesting actions instead of taking them — a critical requirement for autonomous operation.

---

## File Editing Strategy (MVP)

### Mechanism: Anchor-Based Replacement

The agent makes surgical edits using **exact string matching and replacement**:

1. Agent reads the target file via `read_file` → file path and mtime recorded in `FileReadTracker`
2. Agent calls `edit_file(file_path, old_string, new_string)`:
   - **Guard 1:** File must exist
   - **Guard 2:** File must have been read in this session (prevents blind edits)
   - **Guard 3:** File mtime must match the recorded value (prevents stale edits from external modifications)
   - **Guard 4:** `old_string` must differ from `new_string`
   - **Guard 5:** `old_string` must not be empty
   - **Search:** Count occurrences of `old_string` in file content
   - **If 0 matches:** Return error with fuzzy-match hint (whitespace-normalized search) and file excerpt showing context
   - **If >1 matches:** Return error with match count and line numbers of each match
   - **If exactly 1 match:** Snapshot old content via `FileSnapshotStore`, replace, write file, update tracker with new mtime

### How It Locates the Correct Block

The LLM provides `old_string` containing the exact text currently in the file. The uniqueness requirement forces the LLM to include enough surrounding context (e.g., function signature + body, not just one generic line) to unambiguously identify the target.

### What Happens When It Gets the Location Wrong

1. **No match found:** Error includes a whitespace-normalized search that may find a near-miss, plus a file excerpt for orientation. The LLM typically re-reads the file and retries with the correct text.
2. **Multiple matches:** Error includes the line number of every match. The LLM adds more surrounding lines to disambiguate.
3. **After 3+ failed attempts on the same edit:** The circuit breaker may trigger if all tools in the batch fail, or the turn limit (40) prevents infinite retry loops.

### Rollback Support

Every successful edit snapshots the pre-edit file content in `FileSnapshotStore` (in-memory, per-session). The `rollback_file` tool can restore any previous version, enabling the agent to undo edits that break functionality.

### Testing

11 unit tests in `tests/test_edit_file.py` (187 lines) cover:
- Successful single-match edit
- Not-read-first guard rejection
- No-match error with fuzzy hint
- Multiple-match error with line numbers
- Disambiguated edit (adding more context makes match unique)
- Large files (300+ lines, per PRD guidance to test above 200)
- Empty `old_string` rejection
- Identical `old_string`/`new_string` rejection
- File-not-found error
- Whitespace hint behavior
- Sequential edits to the same file

---

## Multi-Agent Design (MVP)

### Orchestration Model: Supervisor Pattern

```
                    ┌────────────────┐
 User Instruction──▶│   Supervisor   │──▶ Final Response
                    │   (LLM-based)  │
                    └───┬────────┬───┘
                        │        │
              delegate  │        │  delegate
                        ▼        ▼
                  ┌─────────┐ ┌───────────┐
                  │  Coder  │ │Researcher │
                  │ (all 11 │ │(read-only │
                  │  tools) │ │  5 tools) │
                  └─────────┘ └───────────┘
```

- **Supervisor:** Receives user instruction, uses an LLM to decide which worker to dispatch to via JSON response `{"delegate_to": "coder" | "researcher", "task": "..."}`, synthesizes final response from worker results.
- **Coder Worker:** Full agent graph with all 11 tools. Handles code reading, editing, writing, command execution, web research, and file rollback.
- **Researcher Worker:** Agent graph with read-only tools (`read_file`, `search_files`, `list_files`, `web_search`, `web_fetch`). Investigates codebases, reviews code, gathers context without modifying files.

### Communication

- Supervisor sends a `HumanMessage` with a task description to the worker.
- Worker runs to completion (full agent loop with its own turn limit) and returns its final AI message as text.
- Supervisor receives only the summary — not the full tool call history (context isolation preserves token budget).

### Output Merging

- The supervisor validates that worker outputs are consistent.
- For sequential tasks (research then code), the researcher's findings inform the coder's instructions.
- For independent tasks, both workers run and the supervisor synthesizes.
- If the supervisor response is plain text (no JSON delegation), it's treated as a direct answer and the flow ends.

### Conflict Resolution

- Workers operate on the same filesystem but the supervisor ensures tasks don't conflict (e.g., two workers editing the same file simultaneously).
- Depth limit: workers cannot spawn sub-agents (no recursive explosion).
- Each worker gets a fresh `AgentState` — the supervisor merges results back into the main conversation.

---

## Trace Links (MVP)

- Trace 1 (normal run — read file + surgical edit): https://smith.langchain.com/public/8c7772fd-b6d6-449b-bf37-80bb78df7b12/r
- Trace 2 (error/recovery path — search for missing method, discover it's absent, add it, verify with test): https://smith.langchain.com/public/24b479c5-a023-4855-947b-92914f28a0e6/r

---

## Architecture Decisions (Final Submission)

### 1. LangGraph over Raw API Loop

**Considered:** Custom async loop with direct Anthropic/OpenAI API calls vs. LangGraph StateGraph.

**Decision:** LangGraph. It provides declarative state management with typed reducers, conditional routing (tool_use → execute_tools, end_turn → END, error → cancel_tools), and automatic LangSmith tracing with zero instrumentation code. The `add_messages` reducer handles message list merging correctly, and `bind_tools` auto-generates tool schemas from function signatures.

**Trade-off:** Framework coupling. If LangGraph introduces breaking changes, migration is non-trivial. But for this project scope, the 500+ lines of saved loop management, the built-in state persistence hooks, and the native tracing outweigh the coupling risk.

### 2. Anchor-Based Editing (Exact String Match)

**Considered:** Line-number-based editing, AST-based patching, unified diff application, full file rewrite.

**Decision:** Exact string match with uniqueness enforcement. This is the same strategy used by Claude Code and aider. The LLM provides `old_string` (the exact text to replace) and `new_string`. The system requires exactly one match.

**Trade-off:** The LLM must reproduce exact source text, which degrades on very large files (>500 lines) where token cost and hallucination risk increase. But for files under 300 lines, it is reliable and produces clean, predictable diffs. Line-number approaches are fragile (one insertion shifts all numbers). AST approaches require per-language parsers. Full rewrites destroy unrelated code. Exact match hits the best balance.

### 3. Passive Response Filter (Banned Phrase Detection)

**Considered:** Structured output mode (force JSON with action/explanation fields), classifier-based detection, no filter (trust the prompt).

**Decision:** Regex-based banned phrase detection with escalating retries. When the LLM says "let me know" or "you should," the agent re-prompts with increasing urgency.

**Trade-off:** Phrase matching is blunt — it can false-positive on legitimate text containing trigger words (e.g., "the `ensure_ascii` flag"). But it solved a critical problem: without it, the LLM defaulted to suggesting actions 30-40% of the time instead of taking them. The escalation ladder gives 3 chances before forcing action. Ongoing tuning was needed (commits `0ab5cec`, `73d32fd`, `5a29947`).

### 4. GPT-4o as Default LLM Provider

**Considered:** Claude Sonnet 4, GPT-4o, GPT-4o-mini.

**Decision:** GPT-4o via OpenAI. The user had significantly more available tokens on the OpenAI API than Anthropic during development. The LLM provider abstraction (`LLM_PROVIDER=openai` in `.env`) made the switch trivial.

**Trade-off:** GPT-4o produces slightly more verbose tool call arguments than Claude and occasionally returns tool calls with empty `content` fields, requiring extraction logic (commit `6fac745`). But the token budget was the binding constraint, and GPT-4o delivered strong results for code generation and editing tasks.

### 5. Context Compaction at 50% of Window

**Considered:** 70% threshold, sliding window (drop oldest N messages), no compaction (rely on short sessions).

**Decision:** Summarize-and-replace at 50% of the 200K context window. Older messages are replaced with an LLM-generated summary; the last 10 messages are preserved.

**Trade-off:** 50% is aggressive — it triggers earlier than necessary, potentially losing useful context from early tool calls. But it provides a safety margin against sudden token spikes (a single `read_file` on a large file can add 20K+ chars). The orphaned-ToolMessage bug (commit `13229e5`) showed that naive compaction can corrupt the message graph — the fix sanitizes boundaries to ensure every `ToolMessage` has a preceding `AIMessage` with matching `tool_calls`.

### 6. SQLite for Session Persistence

**Considered:** In-memory only, Redis, PostgreSQL, file-based JSON.

**Decision:** SQLite with three tables: `sessions`, `session_messages`, `file_read_tracker`. Messages are serialized using LangChain's `messages_to_dict`/`messages_from_dict` utilities.

**Trade-off:** SQLite is single-writer, which limits concurrent session support. But the agent runs one instruction at a time per session, so concurrent writes don't occur. SQLite requires no external service, works on Windows and Linux, and survives process restarts — ideal for a development tool.

### 7. Circuit Breaker: All-Tools-Fail Threshold

**Considered:** Any single tool error triggers breaker, N total errors (regardless of batching), no breaker (rely on turn limit).

**Decision:** Circuit breaker triggers only when ALL tools in a batch fail, tracked over 5 consecutive all-fail batches. Individual tool errors reset the counter.

**Trade-off:** The initial implementation (any single error) caused false shutdowns when 1 tool in a batch of 3 failed while the other 2 succeeded — the agent was making progress but got stopped. The corrected all-fail threshold (commit `ef956b4`) catches genuine dead ends (e.g., repeated authentication failures) without aborting recoverable situations.

---

## Ship Rebuild Log (Final Submission)

The Ship rebuild was executed by pointing the Shipyard agent at the original Ship/FleetGraph codebase and instructing it to rebuild the application from scratch. The output lives at `https://github.com/lramosve/new_ship`. The rebuild produced 24 commits (18 by the agent, 6 during deployment hardening) from 2026-03-25 to 2026-03-26.

### Timeline

| Commit | What Happened |
|--------|---------------|
| `fbe1b06` (Mar 25) | Agent bootstrapped FastAPI project with Project CRUD endpoints. Chose Python/FastAPI instead of preserving the original TypeScript/Express stack. |
| `b76b7c1` (Mar 26) | **Intervention:** Test setup failed — fixtures were misconfigured. Agent fixed after error feedback ("all 8 tests passing"). |
| `8e72489` | Agent implemented JWT authentication and full CRUD dashboard across all entity types. Used SHA-256 for password hashing (insecure — not caught). |
| `ee35d7d` | Agent cleaned up generated artifacts and added .gitignore rules. |
| `beac830` | Agent generated Docker packaging (Dockerfile.backend, Dockerfile for frontend, docker-compose.yml) unprompted. |
| `da1cce0` | Task management backend added: CRUD, status/priority enums, date validation, project/assignee relationships. |
| `addf6cf`–`2a893c5` | Kanban board, Gantt chart, and project management overview views added to frontend and backend. |
| `c06cd04` | WebSocket real-time updates implemented for project management. |
| `3914148` | **Intervention:** Docker container startup failed — migration ran before database directory existed. Agent stabilized the entrypoint script. |
| `dfa885c` | Agent tracked Docker runtime assets and test configuration. |
| `a3fdd9f`–`1786f5d` | Analytics backend and frontend dashboard with server-side filtering. |
| `0931ae7` | **Intervention:** Runtime settings were too loose for production. Agent hardened configuration (required SECRET_KEY in production, environment-specific defaults). |
| `b9ea6a6` | Agent generated GitHub Actions CI pipeline (backend tests, frontend build, Docker build validation, E2E smoke tests). |
| `1e95c5e` | E2E smoke coverage added with Playwright. Project management fallback for empty states. |
| `f7a93f4` | **Intervention:** Production configuration still had issues (CORS, logging, debug flags). Agent hardened further. |

### Interventions Summary

| # | What Broke / Got Stuck | What Was Done | What It Reveals |
|---|----------------------|---------------|-----------------|
| 1 | Test fixtures misconfigured — tests failed on first run | Agent received error output and self-corrected | Agents struggle with test infrastructure setup (conftest.py, session scoping, dependency overrides) because the error messages are indirect |
| 2 | Docker container startup crash — Alembic ran before data directory existed | Agent added `mkdir -p` and reordered entrypoint | Integration boundaries (Docker entrypoint → migration → app startup) are where agents most often fail because they can't test the full chain locally in a single tool call |
| 3 | Runtime settings insufficient for production — no SECRET_KEY validation, debug enabled | Agent added environment-specific defaults and production guards | Non-functional requirements (security, configuration hardening) are invisible to agents unless explicitly prompted — the agent optimized for "tests pass" not "production ready" |
| 4 | Production CORS and logging misconfigured after first hardening pass | Agent iterated on settings.py | Some fixes require multiple iterations because the agent doesn't have a mental model of the full deployment environment — it fixes what the error message says, not the root cause |
| 5 | Language swap (TypeScript → Python) was never corrected | Not intervened — accepted as a design choice | Without explicit instruction to "use the same language as the original," the agent defaults to its most fluent stack. This is a prompt design failure, not an agent capability failure. |
| 6 | FleetGraph agent (proactive polling, anomaly detection) was omitted entirely | Not intervened — out of scope for time constraints | Building an AI agent within an application is a meta-task that requires understanding design rationale, not just API surface. The agent saw entity types and built CRUD; it did not infer that the original had autonomous intelligence. |
| 7 | SHA-256 password hashing instead of bcrypt | Not caught during rebuild — identified in post-analysis | Security best practices must be injected as explicit context. The agent chose the simplest hash that made tests pass. |
| 8 | Monolithic 1,681-line App.tsx | Not intervened — accepted for time constraints | File editing agents gravitate toward fewer, larger files because splitting requires understanding import graphs and shared state mid-stream. |

### Key Metrics

- **Total rebuild time:** ~24 hours (18 commits from fbe1b06 to f7a93f4)
- **Lines of code produced:** ~5,482 (2,887 Python + 2,595 TypeScript)
- **Features implemented:** Authentication, 6 CRUD entity types, Kanban/Gantt views, analytics, WebSocket real-time, Docker packaging, CI/CD, E2E tests
- **Features omitted:** FleetGraph agent, collaborative editing (TipTap/Yjs), audit logging, workspace isolation, RBAC, accessibility (Section 508)

---

## Comparative Analysis (Final Submission)

The full seven-section comparative analysis is in [`docs/Comparative Analysis.pdf`](Comparative%20Analysis.pdf). Key findings:

**Scale difference:** The original Ship/FleetGraph is ~126,000 lines of TypeScript; the rebuild is ~5,500 lines (23:1 ratio). This reflects both genuine simplification and significant feature omission.

**What the agent did well:**
- Generated a working, containerized, tested application in ~24 hours
- Maintained consistent code patterns across all CRUD routers and schemas
- Produced type-safe code (Pydantic validators, TypeScript types) automatically
- Included Docker packaging, CI/CD, and E2E tests without being asked

**What the agent missed:**
- FleetGraph intelligence agent (proactive polling, anomaly detection, HITL approval)
- Real-time collaborative editing (TipTap + Yjs CRDT)
- Unified document model (replaced with conventional normalized tables)
- Government-grade security (PIV auth, CSRF, rate limiting, audit logging, Section 508)
- SHA-256 password hashing instead of bcrypt/argon2

**Root cause of shortcomings:** The agent optimizes for functional correctness ("do tests pass?") not architectural fidelity ("does this match the original's design?"). Without injected context describing the original's architecture, the agent built the simplest thing that satisfied the entity model.

---

## Cost Analysis (Final Submission)

| Item | Amount |
|---|---|
| OpenAI API — input tokens | ~180,000 |
| OpenAI API — output tokens | ~90,000 |
| Total invocations during development | ~362 |
| Total development spend | ~$2.50 |

Note: Development primarily used GPT-4o via OpenAI due to higher available token budget. Claude was used for initial MVP development; OpenAI for the Ship rebuild and iteration.

| 100 Users | 1,000 Users | 10,000 Users |
|---|---|---|
| $75/month | $750/month | $7,500/month |

Assumptions:
- Average agent invocations per user per day: 5
- Average tokens per invocation (input / output): 2,000 / 1,000
- Cost per invocation: ~$0.005 (GPT-4o pricing: $2.50/1M input, $10/1M output)
