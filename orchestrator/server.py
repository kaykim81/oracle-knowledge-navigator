"""FastAPI wrapper around the orchestrator agent.

One endpoint, ``POST /query``, plus ``/health``. The agent's persistent MCP
sessions are opened once on startup (lifespan) and closed on shutdown.

Run: ``uvicorn orchestrator.server:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
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
             len(agent._tools), len(agent._sessions))
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
    """Answer a question; returns {answer, trace, latency_ms}."""
    return await app.state.agent.query(req.question, retrieval_mode=req.retrieval_mode)
