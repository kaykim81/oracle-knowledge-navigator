"""Factory for the per-product MCP servers.

Every Oracle product line gets its own FastMCP server exposing the same three
tools — ``search_docs`` / ``get_document`` / ``list_topics`` — scoped to one
product. Only the product, port, name, instructions, and tool descriptions
vary; the tool bodies, read-only DB wiring, and health route live here so each
``mcp_servers/<product>/server.py`` stays thin.

Imported only by the MCP server processes (needs the ``mcp`` package) — never
by ingestion.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from shared import db, retrieval


def build_server(
    *,
    product: str,
    name: str,
    port: int,
    instructions: str,
    search_description: str,
    get_document_description: str,
    list_topics_description: str,
) -> FastMCP:
    """Build a product-scoped MCP server with the three standard tools."""
    mcp = FastMCP(name, instructions=instructions, host="0.0.0.0", port=port)
    state: dict = {"conn": None}

    def _db():
        if state["conn"] is None:
            state["conn"] = db.connect(read_only=True)  # SQLite bind-mounted :ro
            retrieval.set_db_connection(state["conn"])
        return state["conn"]

    @mcp.tool(description=search_description)
    async def search_docs(query: str, top_k: int = 5, mode: str = "hybrid_rerank") -> list[dict]:
        results = await retrieval.retrieve(query, product, mode, top_k=top_k)
        return [r.model_dump(mode="json") for r in results]

    @mcp.tool(description=get_document_description)
    async def get_document(doc_id: str) -> dict | None:
        document = db.get_document(_db(), doc_id)
        return document.model_dump(mode="json") if document else None

    @mcp.tool(description=list_topics_description)
    async def list_topics() -> list[str]:
        return db.top_level_sections(_db(), product)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return mcp


def run(mcp: FastMCP) -> None:
    """Start the server over MCP streamable-HTTP."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mcp.run(transport="streamable-http")
