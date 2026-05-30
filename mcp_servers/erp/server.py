"""ERP MCP server — Oracle Fusion Cloud ERP (Financials) knowledge.

Thin product-specific config; the tool bodies live in shared.mcp_server.
Run: ``python -m mcp_servers.erp.server`` (listens on :8001).
"""

from shared.mcp_server import build_server, run

INSTRUCTIONS = (
    "Knowledge base for Oracle Fusion Cloud ERP (Financials): General Ledger, "
    "Accounts Payable, Accounts Receivable, and Fixed Assets. Use this server for "
    "ERP questions only; it does not cover EPM, OCI, or Oracle Database."
)

SEARCH_DOCS = """Search Oracle Fusion Cloud ERP documentation; return the most relevant passages.

IN SCOPE: Oracle Fusion Cloud ERP — Financials: General Ledger (journals, periods,
allocations), Accounts Payable (invoices, payments), Accounts Receivable (receipts,
credit to cash), and Fixed Assets.

OUT OF SCOPE: This server does NOT cover Oracle EPM (planning, financial consolidation
and close, narrative reporting), Oracle Database, or OCI infrastructure. For those, use
the appropriate MCP server instead.

BOUNDARY (do not be fooled by shared vocabulary): this server owns *General Ledger*
journals, allocations, and currency *revaluation* of balances. But **consolidation
journals**, **allocation rules in Planning**, and **translating balances to a parent
currency during the financial close** are EPM, not ERP — even though the words
"journal", "allocation", and "translate" appear here too.

Args:
    query: A natural-language question about Oracle Fusion ERP.
    top_k: Number of passages to return (default 5).
    mode: Retrieval mode — "vector_only", "hybrid", or "hybrid_rerank" (default; best).

Returns:
    Ranked passages with chunk text, section path, source URL, score, and latency.
"""

GET_DOCUMENT = (
    "Fetch the full text of an ERP document by its doc_id (from a search_docs "
    "result's chunk) for citation or deeper reading. Returns null if unknown."
)

LIST_TOPICS = (
    "List the top-level ERP topics covered by this server's knowledge base; "
    "useful for understanding coverage before searching."
)

mcp = build_server(
    product="erp",
    name="oracle-erp",
    port=8001,
    instructions=INSTRUCTIONS,
    search_description=SEARCH_DOCS,
    get_document_description=GET_DOCUMENT,
    list_topics_description=LIST_TOPICS,
)

if __name__ == "__main__":
    run(mcp)
