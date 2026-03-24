# Shipyard MVP Demo Script (3–5 minutes)

> Record with terminal visible (Windows PowerShell). Use a clean terminal with large font (16-18pt).
> Shipyard is deployed on Railway — no local server needed.

**Production URL:** https://shipyard-production-610b.up.railway.app
**API Docs (Swagger):** https://shipyard-production-610b.up.railway.app/docs
**LangSmith Dashboard:** https://smith.langchain.com/o/9ec225d0-ceaf-4bba-a026-02438fa14772/projects/p/6a036fa1-fcf9-4af7-8648-e2539bdb54ef
**GitHub Repo:** https://github.com/lramosve/shipyard

---

## Setup (do before recording)

Open PowerShell. Create a demo workspace on the server by sending a write instruction:

```powershell
$body = @{
    instruction = "Create a file called demo_workspace/user_service.py with this content:`n`nclass UserService:`n    def __init__(self, db):`n        self.db = db`n`n    def get_user(self, user_id):`n        query = f`"SELECT * FROM users WHERE id = {user_id}`"`n        return self.db.execute(query)`n`n    def create_user(self, name, email):`n        query = f`"INSERT INTO users (name, email) VALUES ('{name}', '{email}')`"`n        self.db.execute(query)`n        return {`"name`": name, `"email`": email}`n`n    def delete_user(self, user_id):`n        query = f`"DELETE FROM users WHERE id = {user_id}`"`n        self.db.execute(query)`n`n    def list_users(self):`n        return self.db.execute(`"SELECT * FROM users`")"
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/instruction" -Method Post -ContentType "application/json" -Body $body
```

Wait for it to complete, then reset the session:

```powershell
Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/reset" -Method Post
```

Open browser tabs to:
- https://shipyard-production-610b.up.railway.app/docs (Swagger UI)
- https://smith.langchain.com/o/9ec225d0-ceaf-4bba-a026-02438fa14772/projects/p/6a036fa1-fcf9-4af7-8648-e2539bdb54ef (LangSmith Dashboard)

---

## Part 1: Introduction (30 seconds)

**[Show the GitHub repo in browser or VS Code]**

> "This is Shipyard — an autonomous coding agent I built from scratch using LangGraph, Claude, and FastAPI. It's deployed on Railway as a persistent server, makes surgical file edits without rewriting entire files, supports multi-agent coordination, and traces every step with LangSmith. Let me show you how it works."

**[Show the health endpoint in PowerShell]**

```powershell
Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/health"
```

---

## Part 2: Surgical File Editing (90 seconds)

> "I've set up a typical Python service with a deliberate security flaw — it uses string formatting for SQL queries, which is vulnerable to SQL injection. Let's have the agent fix it."

**[Send instruction to fix SQL injection in get_user only]**

```powershell
$body = @{
    instruction = "Read demo_workspace/user_service.py and fix the SQL injection vulnerability in the get_user method. Use parameterized queries instead of f-strings. Only fix get_user - do not touch the other methods yet."
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/instruction" -Method Post -ContentType "application/json" -Body $body
$response
$taskId = $response.task_id
```

> "I'm sending a natural language instruction to the agent. It returns a task ID immediately — the work happens asynchronously."

**[Poll for result]**

```powershell
# Wait a few seconds, then check
Start-Sleep -Seconds 15
Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/status/$taskId"
```

> "The agent read the file, identified the vulnerable line, and made a surgical edit — replacing only the f-string query in get_user with a parameterized query. Notice from the result: only the get_user method changed. The other three methods are completely untouched — same indentation, same formatting, nothing rewritten. That's what surgical editing means."

---

## Part 3: Context Injection (60 seconds)

> "Now let's fix the remaining methods, but this time I'll inject a coding standard as context so the agent follows our team's conventions."

```powershell
$body = @{
    instruction = "Fix the SQL injection vulnerabilities in all remaining methods of demo_workspace/user_service.py."
    context = @(
        @{
            type = "spec"
            source = "coding_standards.md"
            content = "SQL Coding Standards:`n1. Always use parameterized queries with ? placeholders`n2. Use self.db.execute(query, params) where params is a tuple`n3. Add a docstring to every method describing what it does`n4. Return None explicitly from delete operations"
        }
    )
} | ConvertTo-Json -Depth 3

$response = Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/instruction" -Method Post -ContentType "application/json" -Body $body
$taskId = $response.task_id
```

**[Wait and poll for result]**

```powershell
Start-Sleep -Seconds 20
Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/status/$taskId"
```

> "The agent applied the injected coding standards — parameterized queries with tuple params, docstrings added, and delete_user now returns None explicitly. The context was injected at runtime without restarting the server."

---

## Part 4: Multi-Agent Coordination (60 seconds)

> "Shipyard also supports multi-agent coordination. A supervisor agent dispatches work to specialized workers — a researcher for investigation and a coder for changes."

**[Reset session for clean demo]**

```powershell
Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/reset" -Method Post
```

```powershell
$body = @{
    instruction = "First investigate demo_workspace/user_service.py to identify any remaining issues or improvements, then add input validation to create_user - name must be non-empty and email must contain an @ symbol."
    use_supervisor = $true
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/instruction" -Method Post -ContentType "application/json" -Body $body
$taskId = $response.task_id
```

**[Wait and poll for result]**

```powershell
Start-Sleep -Seconds 30
$result = Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/status/$taskId"
$result
$result.result
```

> "The supervisor first dispatched the researcher worker to analyze the file, then used those findings to instruct the coder worker to add validation. Each worker ran in its own isolated context — the supervisor only saw their summaries, not their full tool call history."

---

## Part 5: Tracing with LangSmith (30 seconds)

**[Switch to browser — LangSmith dashboard]**
**URL:** https://smith.langchain.com/o/9ec225d0-ceaf-4bba-a026-02438fa14772/projects/p/6a036fa1-fcf9-4af7-8648-e2539bdb54ef

> "Every agent run is fully traced in LangSmith. Here's the trace for the multi-agent run we just did."

**[Click into the most recent trace, expand the nodes]**

> "You can see the full execution path — the supervisor's decision, the researcher's file reads and analysis, the coder's surgical edit, and token usage at every step. This is critical for debugging agent behavior."

**[Show the shared trace links]**

- Trace 1 (normal run): https://smith.langchain.com/public/8c7772fd-b6d6-449b-bf37-80bb78df7b12/r
- Trace 2 (error/recovery): https://smith.langchain.com/public/24b479c5-a023-4855-947b-92914f28a0e6/r

> "Traces can be shared as public links for review."

---

## Part 6: Persistent Loop (20 seconds)

> "One more thing — the server has been running this entire demo without restarting. Each instruction built on the previous session state. The conversation history, file read tracker, and injected context all persisted across every request."

```powershell
$history = Invoke-RestMethod -Uri "https://shipyard-production-610b.up.railway.app/history"
"Total messages in session: $($history.total)"
```

> "That's the persistent loop requirement — not fire-and-forget, but a continuous session. This is Railway, not serverless — real persistent state."

---

## Closing (15 seconds)

> "That's Shipyard. A coding agent with surgical file editing, context injection, multi-agent coordination, and full observability. Built with LangGraph, Claude, and FastAPI. Deployed on Railway. The LLM provider is abstracted — you can swap Claude for OpenAI by changing one config value. All the code is on GitHub."

**[Show the GitHub repo page briefly]**
**URL:** https://github.com/lramosve/shipyard

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
