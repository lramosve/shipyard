# Shipyard

Autonomous coding agent with surgical file editing, multi-agent coordination, and runtime context injection. Built with LangGraph, Claude, and FastAPI.

**Live API:** https://shipyard-production-610b.up.railway.app
**API Docs:** https://shipyard-production-610b.up.railway.app/docs
**LangSmith Traces:** https://smith.langchain.com/public/8c7772fd-b6d6-449b-bf37-80bb78df7b12/r

## Features

- **Surgical file editing** — anchor-based replacement (`old_string` / `new_string`) that modifies only the targeted code block, never rewrites entire files
- **Persistent agent loop** — FastAPI server maintains session state across instructions without restarting
- **Multi-agent coordination** — supervisor dispatches tasks to specialized coder and researcher workers
- **Context injection** — inject specs, schemas, coding standards, or test results at runtime
- **Full observability** — every LLM call, tool execution, and agent decision traced via LangSmith
- **Swappable LLM** — Claude is the default; switch to OpenAI by changing one environment variable

## Quick Start

### Prerequisites

- Python 3.11+
- An Anthropic API key

### Setup

```bash
git clone https://github.com/lramosve/shipyard.git
cd shipyard
pip install -e .
```

Create a `.env` file:

```
ANTHROPIC_API_KEY=sk-ant-...
LANGSMITH_API_KEY=lsv2_...       # optional, for tracing
LANGSMITH_TRACING=true            # optional
LANGSMITH_PROJECT=shipyard        # optional
```

### Run locally

```bash
python -m shipyard.main
```

The server starts on `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

### Run with Docker

```bash
docker build -t shipyard .
docker run -p 8000:8000 --env-file .env shipyard
```

## Usage

### Submit an instruction

```bash
curl -X POST http://localhost:8000/instruction \
  -H "Content-Type: application/json" \
  -d '{"instruction": "Read src/app.py and add error handling to the main function"}'
```

Response:

```json
{"task_id": "a1b2c3d4", "status": "pending"}
```

### Check task status

```bash
curl http://localhost:8000/status/a1b2c3d4
```

### Inject context at instruction time

```bash
curl -X POST http://localhost:8000/instruction \
  -H "Content-Type: application/json" \
  -d '{
    "instruction": "Update the code to follow the spec",
    "context": [{
      "type": "spec",
      "source": "requirements.md",
      "content": "Functions must validate inputs and return typed results"
    }]
  }'
```

### Use multi-agent mode

```bash
curl -X POST http://localhost:8000/instruction \
  -H "Content-Type: application/json" \
  -d '{
    "instruction": "Investigate the auth module then fix the bug",
    "use_supervisor": true
  }'
```

The supervisor dispatches to a **researcher** (read-only tools) and a **coder** (full tool access), then merges their results.

### Other endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/history` | GET | Conversation history for current session |
| `/context` | POST | Inject context mid-session |
| `/context/file` | POST | Inject context from a file path |
| `/reset` | POST | Clear session state |

## Architecture

```
User Instruction (POST /instruction)
        │
        ▼
┌──────────────────────────────────────┐
│  FastAPI Server (persistent session) │
│  State: messages, file_read_tracker, │
│  injected_context                    │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  LangGraph StateGraph                │
│                                      │
│  call_llm ──► should_continue?       │
│     ▲           │          │         │
│     │       tool_use    end_turn     │
│     │           ▼          ▼         │
│  execute_tools      Return Response  │
└──────────────────────────────────────┘
```

### Tools

| Tool | Description |
|---|---|
| `read_file` | Read file contents with optional line range |
| `edit_file` | Surgical anchor-based replacement with uniqueness enforcement |
| `write_file` | Create new files or full overwrite (read-before-write guard) |
| `execute_cmd` | Run shell commands with timeout and output truncation |
| `search_files` | Regex search across files with glob filtering |
| `list_files` | List directory contents |

### File Editing Strategy

The `edit_file` tool uses **anchor-based replacement**:

1. Agent reads the file via `read_file` (recorded in session tracker)
2. Agent calls `edit_file(file_path, old_string, new_string)`
3. Tool validates: file was read, file hasn't changed since read, `old_string` appears exactly once
4. On success: replaces the match and writes the file
5. On failure: returns a descriptive error (no match, multiple matches, stale read) so the LLM can self-correct

### Multi-Agent Design

```
         ┌────────────────┐
Input ──►│   Supervisor    │──► Response
         └──┬──────────┬──┘
            │          │
            ▼          ▼
       ┌────────┐ ┌───────────┐
       │ Coder  │ │Researcher │
       │(all    │ │(read-only │
       │ tools) │ │ tools)    │
       └────────┘ └───────────┘
```

Workers run in isolated contexts. The supervisor sees only their summaries.

### LLM Provider Abstraction

The LLM is accessed through a Python `Protocol` class. Switch providers by setting `LLM_PROVIDER` in `.env`:

```
LLM_PROVIDER=anthropic   # default
LLM_PROVIDER=openai      # swap to GPT
```

Both `AnthropicProvider` and `OpenAIProvider` implement the same `LLMProvider` protocol.

## Project Structure

```
shipyard/
├── shipyard/
│   ├── main.py              # FastAPI server, endpoints, session management
│   ├── config.py            # Settings from environment variables
│   ├── llm/
│   │   ├── provider.py      # LLMProvider Protocol + response models
│   │   ├── anthropic_provider.py
│   │   └── openai_provider.py
│   ├── tools/
│   │   ├── base.py          # ToolResult model, FileReadTracker
│   │   ├── read_file.py
│   │   ├── edit_file.py     # Anchor-based surgical replacement
│   │   ├── write_file.py
│   │   ├── execute_cmd.py
│   │   ├── search_files.py
│   │   └── list_files.py
│   ├── agent/
│   │   ├── state.py         # LangGraph AgentState TypedDict
│   │   ├── graph.py         # Core agent loop (StateGraph)
│   │   ├── nodes.py         # call_llm, execute_tools, should_continue
│   │   └── supervisor.py    # Multi-agent supervisor graph
│   ├── context/
│   │   └── injection.py     # Format and load external context
│   └── tracing/
│       └── setup.py         # LangSmith configuration
├── tests/
│   ├── test_edit_file.py    # 11 tests for surgical editing
│   └── test_api.py          # FastAPI integration tests
├── docs/
│   ├── PRESEARCH.md         # Pre-search research and architecture decisions
│   ├── CODEAGENT.md         # Agent architecture documentation
│   └── DEMO_SCRIPT_MVP.md   # Demo walkthrough
├── scripts/
│   └── demo_mvp.ps1         # Interactive PowerShell demo script
├── Dockerfile
├── pyproject.toml
└── .env.example
```

## Tests

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Deployment

Deployed on Railway as a persistent container:

```bash
railway up
```

Or deploy via Docker to any platform that supports containers.

## Observability

LangSmith traces every agent run automatically when `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` are set. Each trace shows:

- Every LLM call with token counts
- Every tool call with inputs, outputs, and duration
- Error paths and recovery steps
- Aggregate cost and latency

Shared trace examples:
- [Normal run (read + surgical edit)](https://smith.langchain.com/public/8c7772fd-b6d6-449b-bf37-80bb78df7b12/r)
- [Error/recovery path](https://smith.langchain.com/public/24b479c5-a023-4855-947b-92914f28a0e6/r)

## License

MIT
