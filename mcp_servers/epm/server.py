"""EPM MCP server — Oracle Fusion Cloud EPM knowledge.

Thin product-specific config; the tool bodies live in shared.mcp_server.
Run: ``python -m mcp_servers.epm.server`` (listens on :8003).
"""

from shared.mcp_server import build_server, run

INSTRUCTIONS = (
    "Knowledge base for Oracle Fusion Cloud EPM: Planning, Financial Consolidation "
    "and Close, and Narrative Reporting. Use this server for EPM questions only; it "
    "does not cover Oracle Fusion ERP financials or OCI infrastructure."
)

SEARCH_DOCS = """Search Oracle Fusion Cloud EPM documentation; return the most relevant passages.

IN SCOPE: Oracle Fusion Cloud EPM — Planning (planning modules, forms, business
rules, **allocation rules in Planning**), Financial Consolidation and Close
(consolidation process, **consolidation journals**, **currency translation to a
parent currency as part of the close**, intercompany), and Narrative Reporting.

OUT OF SCOPE: This server does NOT cover Oracle Fusion Cloud ERP (General Ledger,
Payables, Receivables, Assets) or OCI infrastructure. Note the boundary: ERP records
transactions and runs GL journals/allocations/revaluation; EPM consolidates, plans,
and reports on top of that data — so anything qualified by *consolidation*, *Planning*,
or the *close* belongs here, not in ERP. For ERP financials or OCI, use the
appropriate MCP server instead.

Args:
    query: A natural-language question about Oracle EPM.
    top_k: Number of passages to return (default 5).
    mode: Retrieval mode — "vector_only", "hybrid", or "hybrid_rerank" (default; best).

Returns:
    Ranked passages with chunk text, section path, source URL, score, and latency.
"""

GET_DOCUMENT = (
    "Fetch the full text of an EPM document by its doc_id (from a search_docs "
    "result's chunk) for citation or deeper reading. Returns null if unknown."
)

LIST_TOPICS = (
    "List the top-level EPM topics covered by this server's knowledge base; "
    "useful for understanding coverage before searching."
)

mcp = build_server(
    product="epm",
    name="oracle-epm",
    port=8003,
    instructions=INSTRUCTIONS,
    search_description=SEARCH_DOCS,
    get_document_description=GET_DOCUMENT,
    list_topics_description=LIST_TOPICS,
)

if __name__ == "__main__":
    run(mcp)
