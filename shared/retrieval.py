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

import asyncio
import logging
import time

from . import embeddings, qdrant_store
from .models import RetrievalMode, SearchResult

log = logging.getLogger(__name__)

# Lazily-built, reusable Qdrant client (MCP servers call retrieve() repeatedly).
# Tests inject an in-memory client via set_qdrant_client().
_qdrant = None


def _get_qdrant():
    global _qdrant
    if _qdrant is None:
        _qdrant = qdrant_store.get_client()
    return _qdrant


def set_qdrant_client(client) -> None:
    """Override the module's Qdrant client (used by tests / explicit config)."""
    global _qdrant
    _qdrant = client


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
    """Embed the query, search the product's Qdrant collection, return top_k."""
    t0 = time.perf_counter()
    vector = await asyncio.to_thread(embeddings.embed_query, query)
    hits = await asyncio.to_thread(
        qdrant_store.search, _get_qdrant(), product, vector, limit=top_k
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return [
        SearchResult(chunk=chunk, score=score, retrieval_mode="vector_only",
                     latency_ms=latency_ms)
        for chunk, score in hits
    ]


async def _hybrid(query: str, product: str, top_k: int) -> list[SearchResult]:
    raise NotImplementedError("hybrid: implemented in Phase 2 step 3")


async def _hybrid_rerank(query: str, product: str, top_k: int) -> list[SearchResult]:
    raise NotImplementedError("hybrid_rerank: implemented in Phase 2 step 4")
