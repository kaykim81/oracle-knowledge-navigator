"""EPM MCP server — Oracle Fusion Cloud EPM knowledge.

Thin product-specific config; the tool bodies live in shared.mcp_server.
Run: ``python -m mcp_servers.epm.server`` (listens on :8003).

EPM is a *suite* of distinct modules whose guides share one collection, so a
naive search lets a Planning question surface Financial-Consolidation chunks
(they share vocabulary like "scenario"/"version"/"journal"). To stop that
cross-module bleed, this server exposes one **module-scoped** search tool per
module instead of a single ``search_docs`` — each filters to its own source
doc, so routing to the right module's tool *cannot* return another module's
content. Cross-module EPM questions call more than one module tool and synthesize.
"""

from shared.mcp_server import build_server, run

INSTRUCTIONS = (
    "Knowledge base for Oracle Fusion Cloud EPM, a suite of three distinct modules: "
    "Planning, Financial Consolidation and Close (FCC), and Narrative Reporting. "
    "Each has its own module-scoped search tool — pick the one the question is about; "
    "a procedure from one module often does not apply to another. Use this server for "
    "EPM questions only; it does not cover Oracle Fusion ERP financials or OCI."
)

# Args/Returns are identical across the module tools; defined once and appended.
_SEARCH_ARGS = """

Args:
    query: A natural-language question about this EPM module.
    top_k: Number of passages to return (default 5).
    mode: Retrieval mode — "vector_only", "hybrid", or "hybrid_rerank" (default; best).

Returns:
    Ranked passages with chunk text, section path, source URL, score, and latency.
"""

SEARCH_PLANNING = """Search the Oracle EPM **Planning** module docs (Administering Planning Modules).

IN SCOPE: planning and budgeting, forecasting, the Financials/Workforce/Projects/Capital
planning modules, forms, business rules and **allocation rules in Planning**, the
**Scenario and Version dimensions** (creating/editing members, seeded OEP_ members,
budget revisions), Calculation Manager.

NOT THIS MODULE: financial consolidation, consolidation journals, or currency
translation as part of the close → use ``search_fcc``. Management/narrative reports
and disclosure → use ``search_narrative``. ERP General Ledger / Payables /
Receivables / Assets → use the ERP server.""" + _SEARCH_ARGS

SEARCH_FCC = """Search the Oracle EPM **Financial Consolidation and Close (FCC)** module docs.

IN SCOPE: the consolidation process, **consolidation journals**, **currency
translation to a parent currency as part of the close**, intercompany eliminations,
ownership management, the close calendar, FCC-specific dimensions and rules.

NOT THIS MODULE: Planning/budgeting/forecasting, planning scenarios & versions, or
allocation rules in Planning → use ``search_planning``. Narrative/management reports →
use ``search_narrative``. ERP financials → use the ERP server.""" + _SEARCH_ARGS

SEARCH_NARRATIVE = """Search the Oracle EPM **Narrative Reporting** module docs.

IN SCOPE: narrative/management reporting, report packages, doclets, disclosure
management, the narrative authoring and review/sign-off workflow.

NOT THIS MODULE: planning/budgeting → use ``search_planning``; financial consolidation
and the close → use ``search_fcc``; ERP financials → use the ERP server.""" + _SEARCH_ARGS

GET_DOCUMENT = (
    "Fetch the full text of an EPM document by its doc_id (from a search result's "
    "chunk) for citation or deeper reading. Returns null if unknown."
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
    search_description="",  # unused: EPM registers per-module search tools below
    get_document_description=GET_DOCUMENT,
    list_topics_description=LIST_TOPICS,
    module_searches=[
        {"name": "search_planning", "doc_ids": ["epm-planning"], "description": SEARCH_PLANNING},
        {"name": "search_fcc", "doc_ids": ["epm-fcc"], "description": SEARCH_FCC},
        {"name": "search_narrative", "doc_ids": ["epm-narrative-reporting"],
         "description": SEARCH_NARRATIVE},
    ],
)

if __name__ == "__main__":
    run(mcp)
