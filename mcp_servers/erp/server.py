"""ERP MCP server — Oracle Fusion Cloud ERP (Financials) knowledge.

Exposes three tools over MCP streamable-HTTP, backed by the shared hybrid
retrieval engine scoped to the ``erp`` product. This server only ever touches
the ERP collection — the federation boundary is enforced by deployment.

Run: ``python -m mcp_servers.erp.server`` (listens on :8001).
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from shared import db, retrieval

log = logging.getLogger("erp-mcp")

SERVER_INSTRUCTIONS = (
    "Knowledge base for Oracle Fusion Cloud ERP (Financials): General Ledger, "
    "Accounts Payable, Accounts Receivable, and Fixed Assets. Use this server for "
    "ERP questions only; it does not cover EPM, OCI, or Oracle Database."
)

mcp = FastMCP("oracle-erp", instructions=SERVER_INSTRUCTIONS, host="0.0.0.0", port=8001)

# Lazy shared SQLite connection (also used by the retrieval engine).
_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = db.connect()
        retrieval.set_db_connection(_conn)
    return _conn


@mcp.tool()
async def search_docs(query: str, top_k: int = 5, mode: str = "hybrid_rerank") -> list[dict]:
    """Search Oracle Fusion Cloud ERP documentation; return the most relevant passages.

    IN SCOPE: Oracle Fusion Cloud ERP — Financials: General Ledger (journals,
    periods, allocations), Accounts Payable (invoices, payments), Accounts
    Receivable (receipts, credit to cash), and Fixed Assets.

    OUT OF SCOPE: This server does NOT cover Oracle EPM (planning, financial
    consolidation and close, narrative reporting), Oracle Database, or OCI
    infrastructure. For those, use the appropriate MCP server instead.

    Args:
        query: A natural-language question about Oracle Fusion ERP.
        top_k: Number of passages to return (default 5).
        mode: Retrieval mode — "vector_only", "hybrid", or "hybrid_rerank"
            (default; highest quality, reranked).

    Returns:
        Ranked passages, each with the chunk text, section path, source URL,
        relevance score, and retrieval latency.
    """
    results = await retrieval.retrieve(query, "erp", mode, top_k=top_k)
    return [r.model_dump(mode="json") for r in results]


@mcp.tool()
async def get_document(doc_id: str) -> dict | None:
    """Fetch the full text of an ERP document by its ``doc_id``.

    Use the ``doc_id`` from a ``search_docs`` result's chunk to retrieve the
    whole document (all sections joined) for citation or deeper reading.
    Returns null if the document is unknown to this server.
    """
    document = db.get_document(_db(), doc_id)
    return document.model_dump(mode="json") if document else None


@mcp.tool()
async def list_topics() -> list[str]:
    """List the top-level ERP topics covered by this server's knowledge base.

    Useful for understanding coverage/scope before searching.
    """
    return db.top_level_sections(_db(), "erp")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mcp.run(transport="streamable-http")
