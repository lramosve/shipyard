"""Integration tests for the FastAPI server."""

import asyncio
import os
import time

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("LANGSMITH_TRACING", "false")

from shipyard.main import app, session


@pytest.fixture(autouse=True)
async def reset():
    """Reset session state between tests."""
    session.file_read_tracker.clear()
    session.injected_context.clear()
    session.messages.clear()
    session.tasks.clear()
    yield


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_inject_context(client):
    resp = await client.post("/context", json={
        "type": "spec",
        "source": "test.md",
        "content": "This is a test spec.",
    })
    assert resp.status_code == 200
    assert resp.json()["total_context_items"] == 1


@pytest.mark.asyncio
async def test_history_empty(client):
    resp = await client.get("/history")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_reset(client):
    # Add some context
    await client.post("/context", json={"type": "spec", "source": "x", "content": "y"})
    assert len(session.injected_context) == 1

    # Reset
    resp = await client.post("/reset")
    assert resp.status_code == 200
    assert len(session.injected_context) == 0


@pytest.mark.asyncio
async def test_submit_instruction_returns_task_id(client):
    resp = await client.post("/instruction", json={"instruction": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert data["status"] in ("pending", "running")


@pytest.mark.asyncio
async def test_status_not_found(client):
    resp = await client.get("/status/nonexistent")
    assert resp.status_code == 404
