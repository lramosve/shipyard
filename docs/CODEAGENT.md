# CODEAGENT.md — Shipyard

## Agent Architecture (MVP)

### Overview

Shipyard is a LangGraph-based autonomous coding agent with a FastAPI persistent server. The architecture follows a **single-threaded agent loop** with **supervisor-dispatched multi-agent coordination**.

### Agent Loop Design

```
User Instruction (HTTP POST /instruction)
    │
    ▼
┌─────────────────────────────────────────────────┐
│  FastAPI Server (persistent — never restarts)    │
│  Session state: messages, file_read_tracker,     │
│  injected_context                                │
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
└─────────────────────────────────────────────────┘
```

### State Management

State is defined as a LangGraph `TypedDict` with:
- `messages` — full conversation history (LangGraph `add_messages` reducer handles appending)
- `file_read_tracker` — dict mapping file paths to timestamps of when they were last read (for edit validation)
- `injected_context` — list of context items (type, source, content) injected at runtime
- `working_directory` — the root directory the agent operates in

State persists across instructions within a session. New instructions append to existing message history — the agent maintains context from prior turns.

### Tool Calls

Tools are defined as LangChain `@tool`-decorated functions for schema generation, but executed by a custom `execute_tools` node that calls the actual tool implementations with session state (e.g., the `FileReadTracker`). This separation keeps tool schemas clean while allowing stateful execution.

Six tools are available:
1. `read_file` — read file contents with optional line range
2. `edit_file` — anchor-based surgical replacement
3. `write_file` — create new files or full overwrite
4. `execute_cmd` — run shell commands with timeout
5. `search_files` — regex search across files
6. `list_files` — list directory contents

### Entry and Exit Conditions

**Normal run:** User submits instruction → agent loops (call_llm → execute_tools → call_llm → ...) until the LLM produces a text-only response (no tool calls) → returns final response.

**Error branches:**
- Tool execution error → error returned as `ToolMessage` with `status="error"` → LLM sees error and can self-correct
- File not read before edit → immediate error returned, LLM learns to read first
- Stale file (modified externally) → error returned, LLM re-reads
- Multiple matches for `old_string` → error with line numbers, LLM adds more context
- Command timeout → error returned, LLM can retry or adjust

### LLM Provider Abstraction

The LLM is accessed through a Python `Protocol` class (`LLMProvider`) that both `AnthropicProvider` and `OpenAIProvider` implement. For the LangGraph integration, `langchain-anthropic`'s `ChatAnthropic` is used directly (native LangGraph compatibility). The provider abstraction proves swappability — changing `LLM_PROVIDER=openai` in `.env` switches the underlying model.

### Tracing

LangSmith tracing is enabled via environment variables. Every LLM call, tool execution, and state transition is automatically traced. Traces are viewable as shareable links in the LangSmith dashboard.

---

## File Editing Strategy (MVP)

### Mechanism: Anchor-Based Replacement

The agent makes surgical edits using **exact string matching and replacement**:

1. Agent reads the target file via `read_file` → file path and read timestamp recorded in `FileReadTracker`
2. Agent calls `edit_file(file_path, old_string, new_string)`:
   - **Validation step 1:** Verify file was read in this session (prevents blind edits)
   - **Validation step 2:** Verify file hasn't been modified since last read (prevents stale edits)
   - **Validation step 3:** Verify `old_string` is non-empty and differs from `new_string`
   - **Search:** Count occurrences of `old_string` in file content
   - **If 0 matches:** Return error with fuzzy-match hint (whitespace-normalized search) and file excerpt
   - **If >1 matches:** Return error with match count and line numbers of each match
   - **If exactly 1 match:** Replace, write file, update tracker

### How It Locates the Correct Block

The LLM provides `old_string` containing the exact text currently in the file. The uniqueness requirement forces the LLM to include enough surrounding context (e.g., function signature + body, not just one generic line) to unambiguously identify the target.

### What Happens When It Gets the Location Wrong

1. **No match found:** Error includes a whitespace-normalized search that may find a near-miss, plus a file excerpt for orientation. The LLM typically re-reads the file and retries with the correct text.
2. **Multiple matches:** Error includes the line number of every match. The LLM adds more surrounding lines to disambiguate.
3. **After 3+ failed attempts on the same edit:** The agent should escalate to the user (behavior guided by the system prompt).

### Testing

The edit tool has been tested against:
- Small files (<20 lines)
- Large files (300+ lines, per PRD guidance to test above 200)
- Zero-match, multi-match, stale-read, unread-file, empty-string, and identical-string error paths
- Sequential edits to the same file
- Whitespace hint behavior

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
                  │ (all    │ │(read-only │
                  │  tools) │ │  tools)   │
                  └─────────┘ └───────────┘
```

- **Supervisor:** Receives user instruction, uses an LLM to decide which worker to dispatch to, synthesizes final response from worker results.
- **Coder Worker:** Full agent graph with all 6 tools. Handles code reading, editing, writing, and command execution.
- **Researcher Worker:** Agent graph with read-only tools (`read_file`, `search_files`, `list_files`). Investigates codebases, reviews code, gathers context.

### Communication

- Supervisor sends a `HumanMessage` with a task description to the worker.
- Worker runs to completion (full agent loop) and returns its final AI message as text.
- Supervisor receives only the summary — not the full tool call history (context isolation).

### Output Merging

- The supervisor validates that worker outputs are consistent.
- For sequential tasks (research then code), the researcher's findings inform the coder's instructions.
- For independent tasks, both workers run and the supervisor synthesizes.

### Conflict Resolution

- Workers operate on the same filesystem but the supervisor ensures tasks don't conflict (e.g., two workers editing the same file simultaneously).
- Depth limit: workers cannot spawn sub-agents (no recursive explosion).

---

## Trace Links (MVP)

- Trace 1 (normal run — read file + surgical edit): https://smith.langchain.com/public/8c7772fd-b6d6-449b-bf37-80bb78df7b12/r
- Trace 2 (error/recovery path — search for missing method, discover it's absent, add it, verify with test): https://smith.langchain.com/public/24b479c5-a023-4855-947b-92914f28a0e6/r

---

## Architecture Decisions (Final Submission)

_(To be completed for Final Submission)_

---

## Ship Rebuild Log (Final Submission)

_(To be completed for Final Submission)_

---

## Comparative Analysis (Final Submission)

_(To be completed for Final Submission)_

---

## Cost Analysis (Final Submission)

| Item | Amount |
|---|---|
| Claude API — input tokens | |
| Claude API — output tokens | |
| Total invocations during development | |
| Total development spend | |

| 100 Users | 1,000 Users | 10,000 Users |
|---|---|---|
| $___/month | $___/month | $___/month |

Assumptions:
- Average agent invocations per user per day:
- Average tokens per invocation (input / output):
- Cost per invocation:
