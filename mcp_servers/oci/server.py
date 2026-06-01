"""OCI MCP server — Oracle Cloud Infrastructure knowledge.

Thin product-specific config; the tool bodies live in shared.mcp_server.
Run: ``python -m mcp_servers.oci.server`` (listens on :8002).
"""

from shared.mcp_server import build_server, run

INSTRUCTIONS = (
    "Knowledge base for Oracle Cloud Infrastructure (OCI): Compute, Networking, "
    "Object Storage, and IAM. Use this server for OCI infrastructure questions only; "
    "it does not cover Oracle Fusion ERP financials or EPM."
)

SEARCH_DOCS = """Search Oracle Cloud Infrastructure (OCI) documentation; return the most relevant passages.

IN SCOPE: Oracle Cloud Infrastructure — Compute (instances, shapes, images),
Networking (VCNs, subnets, gateways, security rules, BYOIP), Object Storage (buckets,
objects, **storage tiers including Archive for storing/archiving any files — including
financial statements or reports — at low cost**, replication), and IAM (users, groups,
**policies that control which users can access which resources**, **compartments that
isolate any data — including budget or financial data — into its own space with separate
access control**, federation).

OUT OF SCOPE: This server does NOT cover Oracle Fusion Cloud ERP (financials) or
Oracle EPM (planning, consolidation, narrative reporting). For those, use the
appropriate MCP server instead.

BOUNDARY: the cloud *infrastructure* around data is OCI even when the data is financial
or budget content — the subject of the data doesn't change the product. **Storing or
archiving files (financial statements, reports, anything) in Object Storage is OCI.**
**Isolating any data — including budget data — into its own compartment with separate
access is OCI IAM.** A **policy governing which users can access which resources** is OCI
IAM. ERP/EPM own the financial *content and processes*; OCI owns the cloud *storage,
isolation, and access control* around it.

Args:
    query: A natural-language question about OCI infrastructure.
    top_k: Number of passages to return (default 5).
    mode: Retrieval mode — "vector_only", "hybrid", or "hybrid_rerank" (default; best).

Returns:
    Ranked passages with chunk text, section path, source URL, score, and latency.
"""

GET_DOCUMENT = (
    "Fetch the full text of an OCI document by its doc_id (from a search_docs "
    "result's chunk) for citation or deeper reading. Returns null if unknown."
)

LIST_TOPICS = (
    "List the top-level OCI topics covered by this server's knowledge base; "
    "useful for understanding coverage before searching."
)

mcp = build_server(
    product="oci",
    name="oracle-oci",
    port=8002,
    instructions=INSTRUCTIONS,
    search_description=SEARCH_DOCS,
    get_document_description=GET_DOCUMENT,
    list_topics_description=LIST_TOPICS,
)

if __name__ == "__main__":
    run(mcp)
