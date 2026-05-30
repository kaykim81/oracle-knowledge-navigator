"""FastAPI wrapper around the orchestrator agent.

Endpoints: ``POST /query`` (non-streaming JSON, used by the eval),
``POST /query/stream`` (Server-Sent Events, used by the UI), and ``/health``.
The agent discovers each MCP server's tools once on startup (lifespan); MCP
sessions are opened per request inside the agent, not held open here.

Run: ``uvicorn orchestrator.server:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orchestrator.agent import OrchestratorAgent

log = logging.getLogger("orchestrator.server")


class QueryRequest(BaseModel):
    question: str
    # Pinning a mode forces search_docs to use it (the eval runner compares modes).
    retrieval_mode: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    agent = OrchestratorAgent()
    await agent.connect()
    app.state.agent = agent
    log.info("orchestrator ready: %d tools across %d servers",
             len(agent._tools), len(agent.servers))
    try:
        yield
    finally:
        await agent.aclose()


app = FastAPI(title="Oracle Knowledge Navigator — Orchestrator", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/query")
async def query(req: QueryRequest) -> dict:
    """Answer a question; returns {answer, trace, latency_ms}.

    Non-streaming. This is the contract the eval runner depends on — keep it.
    """
    return await app.state.agent.query(req.question, retrieval_mode=req.retrieval_mode)


@app.post("/query/stream")
async def query_stream(req: QueryRequest) -> StreamingResponse:
    """Answer a question, streamed as Server-Sent Events.

    Each SSE ``data:`` line is one JSON event with a ``type`` field:
    ``tool_call`` (trace builds live), ``answer_delta`` (answer streams),
    ``done`` (final trace + latency). The UI renders federation as it happens.
    """
    async def events():
        async for event in app.state.agent.query_stream(
            req.question, retrieval_mode=req.retrieval_mode
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # Disable proxy buffering so events reach the browser as they're produced.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
