"""Hybrid retrieval engine over the Qdrant vector store + SQLite/FTS5 BM25 index.

One entry point, ``retrieve()``, with three modes that the eval scorecard
compares:

- ``vector_only``   — semantic search in Qdrant (step 2)
- ``hybrid``        — vector + BM25 fused with Reciprocal Rank Fusion (step 3)
- ``hybrid_rerank`` — hybrid candidates re-scored by Voyage rerank-2 (step 4)

This is the engine all three MCP servers call. ``product`` is a plain string
(e.g. "erp") so nothing here hardcodes the product list — it maps to the
``{product}_docs`` Qdrant collection and filters SQLite by that value.

Smoke test (step 5)::

    python -m shared.retrieval --query "how do I configure tax rates" \\
        --product erp --mode hybrid_rerank
"""

from __future__ import annotations

import logging

from .models import RetrievalMode, SearchResult

log = logging.getLogger(__name__)


async def retrieve(
    query: str,
    product: str,
    mode: RetrievalMode,
    top_k: int = 10,
) -> list[SearchResult]:
    """Retrieve the top_k chunks for a query within one product, by mode."""
    if mode == "vector_only":
        return await _vector_only(query, product, top_k)
    if mode == "hybrid":
        return await _hybrid(query, product, top_k)
    if mode == "hybrid_rerank":
        return await _hybrid_rerank(query, product, top_k)
    raise ValueError(f"unknown retrieval mode: {mode!r}")


async def _vector_only(query: str, product: str, top_k: int) -> list[SearchResult]:
    raise NotImplementedError("vector_only: implemented in Phase 2 step 2")


async def _hybrid(query: str, product: str, top_k: int) -> list[SearchResult]:
    raise NotImplementedError("hybrid: implemented in Phase 2 step 3")


async def _hybrid_rerank(query: str, product: str, top_k: int) -> list[SearchResult]:
    raise NotImplementedError("hybrid_rerank: implemented in Phase 2 step 4")
