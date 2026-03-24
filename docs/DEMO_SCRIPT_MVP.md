# Shipyard MVP Demo Script (3–5 minutes)

> Record with terminal visible. Use a clean terminal with large font (16-18pt).
> Have the FastAPI server running before you start recording.

---

## Setup (do before recording)

```bash
cd C:\Users\lramo\Documents\GauntletAI\Repo\shipyard

# Create the demo workspace with a realistic file
mkdir -p demo_workspace
```

Create `demo_workspace/user_service.py`:
```python
class UserService:
    def __init__(self, db):
        self.db = db

    def get_user(self, user_id):
        query = f"SELECT * FROM users WHERE id = {user_id}"
        return self.db.execute(query)

    def create_user(self, name, email):
        query = f"INSERT INTO users (name, email) VALUES ('{name}', '{email}')"
        self.db.execute(query)
        return {"name": name, "email": email}

    def delete_user(self, user_id):
        query = f"DELETE FROM users WHERE id = {user_id}"
        self.db.execute(query)

    def list_users(self):
        return self.db.execute("SELECT * FROM users")
```

Start the server in a separate terminal:
```bash
python -m shipyard.main
```

Open a browser tab to `http://localhost:8000/docs` (Swagger UI).
Open a browser tab to LangSmith dashboard.

---

## Part 1: Introduction (30 seconds)

**[Show the repo in VS Code or terminal]**

> "This is Shipyard — an autonomous coding agent I built from scratch using LangGraph, Claude, and FastAPI. It runs as a persistent server, makes surgical file edits without rewriting entire files, supports multi-agent coordination, and traces every step with LangSmith. Let me show you how it works."

---

## Part 2: Surgical File Editing (90 seconds)

**[Show the demo file in the terminal]**

```bash
cat demo_workspace/user_service.py
```

> "Here's a typical Python service with a deliberate security flaw — it uses string formatting for SQL queries, which is vulnerable to SQL injection. Let's have the agent fix it."

**[Switch to another terminal / or use curl]**

```bash
curl -s -X POST http://localhost:8000/instruction \
  -H "Content-Type: application/json" \
  -d '{
    "instruction": "Read demo_workspace/user_service.py and fix the SQL injection vulnerability in the get_user method. Use parameterized queries instead of f-strings. Only fix get_user — do not touch the other methods yet."
  }' | python -m json.tool
```

> "I'm sending a natural language instruction to the agent. It returns a task ID immediately — the work happens asynchronously."

**[Poll for result]**

```bash
# Replace {task_id} with the actual ID returned
curl -s http://localhost:8000/status/{task_id} | python -m json.tool
```

> "The agent read the file, identified the vulnerable line, and made a surgical edit — replacing only the f-string query in get_user with a parameterized query. Let's verify."

**[Show the edited file]**

```bash
cat demo_workspace/user_service.py
```

> "Notice: only the get_user method changed. The other three methods are completely untouched — same indentation, same formatting, nothing rewritten. That's what surgical editing means."

---

## Part 3: Context Injection (60 seconds)

> "Now let's fix the remaining methods, but this time I'll inject a coding standard as context so the agent follows our team's conventions."

```bash
curl -s -X POST http://localhost:8000/instruction \
  -H "Content-Type: application/json" \
  -d '{
    "instruction": "Fix the SQL injection vulnerabilities in all remaining methods of demo_workspace/user_service.py.",
    "context": [{
      "type": "spec",
      "source": "coding_standards.md",
      "content": "SQL Coding Standards:\n1. Always use parameterized queries with ? placeholders\n2. Use self.db.execute(query, params) where params is a tuple\n3. Add a docstring to every method describing what it does\n4. Return None explicitly from delete operations"
    }]
  }' | python -m json.tool
```

**[Wait and poll for result, then show the file]**

```bash
curl -s http://localhost:8000/status/{task_id} | python -m json.tool
cat demo_workspace/user_service.py
```

> "The agent applied the injected coding standards — parameterized queries with tuple params, docstrings added, and delete_user now returns None explicitly. The context was injected at runtime without restarting the server."

---

## Part 4: Multi-Agent Coordination (60 seconds)

> "Shipyard also supports multi-agent coordination. A supervisor agent dispatches work to specialized workers — a researcher for investigation and a coder for changes."

**[Reset session for clean demo]**

```bash
curl -s -X POST http://localhost:8000/reset
```

```bash
curl -s -X POST http://localhost:8000/instruction \
  -H "Content-Type: application/json" \
  -d '{
    "instruction": "First investigate demo_workspace/user_service.py to identify any remaining issues or improvements, then add input validation to create_user — name must be non-empty and email must contain an @ symbol.",
    "use_supervisor": true
  }' | python -m json.tool
```

**[Wait and poll for result]**

```bash
curl -s http://localhost:8000/status/{task_id} | python -m json.tool
```

> "The supervisor first dispatched the researcher worker to analyze the file, then used those findings to instruct the coder worker to add validation. Each worker ran in its own isolated context — the supervisor only saw their summaries, not their full tool call history."

**[Show final file]**

```bash
cat demo_workspace/user_service.py
```

---

## Part 5: Tracing with LangSmith (30 seconds)

**[Switch to browser — LangSmith dashboard]**

> "Every agent run is fully traced in LangSmith. Here's the trace for the multi-agent run we just did."

**[Click into the most recent trace, expand the nodes]**

> "You can see the full execution path — the supervisor's decision, the researcher's file reads and analysis, the coder's surgical edit, and token usage at every step. This is critical for debugging agent behavior."

**[Show one of the shared trace links]**

> "Traces can be shared as public links for review."

---

## Part 6: Persistent Loop (20 seconds)

> "One more thing — the server has been running this entire demo without restarting. Each instruction built on the previous session state. The conversation history, file read tracker, and injected context all persisted across every request."

```bash
curl -s http://localhost:8000/history | python -m json.tool | head -5
```

> "That's the persistent loop requirement — not fire-and-forget, but a continuous session."

---

## Closing (15 seconds)

> "That's Shipyard. A coding agent with surgical file editing, context injection, multi-agent coordination, and full observability. Built with LangGraph, Claude, and FastAPI. The LLM provider is abstracted — you can swap Claude for OpenAI by changing one config value. All the code is on GitHub."

**[Show the GitHub repo page briefly]**

---

## Timing Summary

| Segment | Duration |
|---|---|
| Introduction | 0:30 |
| Surgical Editing | 1:30 |
| Context Injection | 1:00 |
| Multi-Agent | 1:00 |
| Tracing | 0:30 |
| Persistent Loop | 0:20 |
| Closing | 0:15 |
| **Total** | **~5:05** |

> If running long, cut the persistent loop segment — it's demonstrated implicitly throughout.
