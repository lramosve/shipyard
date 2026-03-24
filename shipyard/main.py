"""FastAPI server — persistent agent loop that accepts instructions without restarting."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from enum import Enum

from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from shipyard.agent.graph import build_agent_graph
from shipyard.agent.nodes import reset_snapshot_store
from shipyard.agent.supervisor import build_supervisor_graph
from shipyard.config import settings
from shipyard.context.injection import load_context_from_file
from shipyard.persistence import SessionStore
from shipyard.tracing.setup import configure_tracing


# ---------------------------------------------------------------------------
# Tracing — configured at import time so every LLM call is traced
# ---------------------------------------------------------------------------
configure_tracing()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ContextItem(BaseModel):
    type: str = "general"
    source: str = "inline"
    content: str


class InstructionRequest(BaseModel):
    instruction: str
    context: list[ContextItem] | None = None
    use_supervisor: bool = False


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    result: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Session state — persists across instructions
# ---------------------------------------------------------------------------

class SessionState:
    def __init__(self):
        self.session_id = "default"
        self.file_read_tracker: dict[str, float] = {}
        self.injected_context: list[dict] = []
        self.messages: list = []
        self.tasks: dict[str, TaskResponse] = {}
        self.agent = build_agent_graph()
        self.supervisor = build_supervisor_graph()
        self.store = SessionStore()
        self._lock = asyncio.Lock()
        # Try to restore previous session
        self._restore()

    def _restore(self):
        """Restore session from SQLite if available."""
        msgs = self.store.load_messages(self.session_id)
        if msgs:
            self.messages = msgs
            self.file_read_tracker = self.store.load_file_tracker(self.session_id)
            self.injected_context = self.store.load_context(self.session_id)

    def persist(self):
        """Save current state to SQLite."""
        self.store.create_session(self.session_id, settings.working_directory)
        self.store.save_messages(self.session_id, self.messages)
        self.store.save_file_tracker(self.session_id, self.file_read_tracker)
        self.store.save_context(self.session_id, self.injected_context)


session = SessionState()


# ---------------------------------------------------------------------------
# Background task processing
# ---------------------------------------------------------------------------

async def process_instruction(task_id: str, request: InstructionRequest):
    """Process an instruction asynchronously."""
    async with session._lock:
        session.tasks[task_id].status = TaskStatus.RUNNING

        try:
            # Add any new context
            if request.context:
                for ctx in request.context:
                    session.injected_context.append(ctx.model_dump())

            # Build input state
            new_message = HumanMessage(content=request.instruction)
            session.messages.append(new_message)

            state = {
                "messages": list(session.messages),
                "file_read_tracker": dict(session.file_read_tracker),
                "injected_context": list(session.injected_context),
                "working_directory": settings.working_directory,
                "consecutive_errors": 0,
            }

            # Run the appropriate graph
            if request.use_supervisor:
                result = await asyncio.to_thread(session.supervisor.invoke, state)
            else:
                result = await asyncio.to_thread(session.agent.invoke, state)

            # Update session state with results
            session.messages = result.get("messages", session.messages)
            session.file_read_tracker = result.get("file_read_tracker", session.file_read_tracker)

            # Extract final response
            final_text = ""
            for msg in reversed(result.get("messages", [])):
                if hasattr(msg, "content") and msg.content and msg.type == "ai":
                    final_text = msg.content if isinstance(msg.content, str) else str(msg.content)
                    break

            session.tasks[task_id].status = TaskStatus.COMPLETED
            session.tasks[task_id].result = final_text

            # Persist to SQLite
            session.persist()

        except Exception as e:
            session.tasks[task_id].status = TaskStatus.FAILED
            session.tasks[task_id].error = str(e)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    yield


app = FastAPI(
    title="Shipyard",
    description="Autonomous coding agent with surgical file editing",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok", "model": settings.llm_model, "provider": settings.llm_provider}


@app.post("/instruction", response_model=TaskResponse)
async def submit_instruction(request: InstructionRequest):
    """Submit a new instruction to the agent. Returns a task ID for tracking."""
    task_id = str(uuid.uuid4())[:8]
    task = TaskResponse(task_id=task_id, status=TaskStatus.PENDING)
    session.tasks[task_id] = task

    asyncio.create_task(process_instruction(task_id, request))

    return task


@app.get("/status/{task_id}", response_model=TaskResponse)
async def get_status(task_id: str):
    """Check the status of a submitted instruction."""
    if task_id not in session.tasks:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return session.tasks[task_id]


@app.post("/context")
async def inject_context(item: ContextItem):
    """Inject external context into the current session."""
    session.injected_context.append(item.model_dump())
    return {"status": "ok", "total_context_items": len(session.injected_context)}


@app.post("/context/file")
async def inject_context_from_file(file_path: str, context_type: str = "file"):
    """Load and inject context from a file on disk."""
    try:
        item = load_context_from_file(file_path, context_type)
        session.injected_context.append(item)
        return {"status": "ok", "source": item["source"], "total_context_items": len(session.injected_context)}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/history")
async def get_history():
    """Retrieve conversation history for the current session."""
    history = []
    for msg in session.messages:
        history.append({
            "type": msg.type,
            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
        })
    return {"messages": history, "total": len(history)}


@app.post("/reset")
async def reset_session():
    """Reset the session state."""
    session.file_read_tracker.clear()
    session.injected_context.clear()
    session.messages.clear()
    session.tasks.clear()
    session.agent = build_agent_graph()
    session.supervisor = build_supervisor_graph()
    session.store.delete_session(session.session_id)
    reset_snapshot_store()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    import uvicorn
    uvicorn.run("shipyard.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
