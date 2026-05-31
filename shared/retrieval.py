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

import argparse
import asyncio
import logging
import os
import time

from . import db, embeddings, qdrant_store
from .models import RetrievalMode, SearchResult

log = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant (standard value).
RRF_K = 60
# How many hybrid candidates to send to the reranker before taking top_k.
RERANK_CANDIDATES = 30

# Relevance floor for hybrid_rerank: drop results scoring below this so a query
# with no genuinely relevant chunk returns [] and the orchestrator abstains
# ("say you don't know") instead of answering from general knowledge. Only the
# Voyage rerank-2 score is calibrated relevance — vector cosine and RRF scores
# are not — so the floor applies in hybrid_rerank only. Off-topic results were
# observed at ~0.03; 0.1 is a conservative cut. PROVISIONAL: validate against
# the retrieval eval (recall@k must not drop) and tune via the env var.
MIN_RERANK_SCORE = float(os.getenv("RETRIEVAL_MIN_RERANK_SCORE", "0.1"))


def _apply_score_floor(results: list[SearchResult], floor: float) -> list[SearchResult]:
    """Drop results whose (calibrated rerank) score is below ``floor``.

    ``floor <= 0`` disables filtering. An all-below-floor result set becomes
    ``[]``, which the caller treats as "nothing relevant retrieved".
    """
    if floor <= 0:
        return results
    return [r for r in results if r.score >= floor]

# Lazily-built, reusable clients (MCP servers call retrieve() repeatedly).
# Tests inject in-memory instances via set_qdrant_client() / set_db_connection().
_qdrant = None
_db = None


def _get_qdrant():
    global _qdrant
    if _qdrant is None:
        _qdrant = qdrant_store.get_client()
    return _qdrant


def set_qdrant_client(client) -> None:
    """Override the module's Qdrant client (used by tests / explicit config)."""
    global _qdrant
    _qdrant = client


def _get_db():
    global _db
    if _db is None:
        _db = db.connect()
    return _db


def set_db_connection(conn) -> None:
    """Override the module's SQLite connection (used by tests / explicit config)."""
    global _db
    _db = conn


def _rrf_fuse(ranked_lists, top_k):
    """Reciprocal Rank Fusion: score(d) = sum_rankers 1 / (RRF_K + rank)."""
    scores: dict[str, float] = {}
    chunks: dict[str, object] = {}
    for hits in ranked_lists:
        for rank, (chunk, _score) in enumerate(hits, start=1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (RRF_K + rank)
            chunks[chunk.id] = chunk
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [(chunks[cid], score) for cid, score in ordered]


async def retrieve(
    query: str,
    product: str,
    mode: RetrievalMode,
    top_k: int = 10,
    *,
    min_rerank_score: float | None = None,
) -> list[SearchResult]:
    """Retrieve the top_k chunks for a query within one product, by mode.

    In ``hybrid_rerank``, results below the relevance floor are dropped (see
    ``MIN_RERANK_SCORE``); ``min_rerank_score`` overrides that default.
    """
    if mode == "vector_only":
        return await _vector_only(query, product, top_k)
    if mode == "hybrid":
        return await _hybrid(query, product, top_k)
    if mode == "hybrid_rerank":
        return await _hybrid_rerank(query, product, top_k, min_rerank_score)
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
    """Run vector + BM25 in parallel, fuse with RRF, return top_k."""
    t0 = time.perf_counter()
    pool = max(top_k, 30)  # candidate depth fetched from each ranker before fusion

    async def vector_leg():
        vector = await asyncio.to_thread(embeddings.embed_query, query)
        return await asyncio.to_thread(
            qdrant_store.search, _get_qdrant(), product, vector, limit=pool
        )

    async def bm25_leg():
        return await asyncio.to_thread(
            db.search_bm25, _get_db(), query, product=product, limit=pool
        )

    vector_hits, bm25_hits = await asyncio.gather(vector_leg(), bm25_leg())
    fused = _rrf_fuse([vector_hits, bm25_hits], top_k)
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return [
        SearchResult(chunk=chunk, score=score, retrieval_mode="hybrid",
                     latency_ms=latency_ms)
        for chunk, score in fused
    ]


async def _hybrid_rerank(
    query: str, product: str, top_k: int, min_rerank_score: float | None = None
) -> list[SearchResult]:
    """Hybrid-retrieve candidates, re-score with Voyage rerank-2, drop sub-floor."""
    t0 = time.perf_counter()
    candidates = await _hybrid(query, product, RERANK_CANDIDATES)
    if not candidates:
        return []
    docs = [c.chunk.text for c in candidates]
    tr0 = time.perf_counter()
    ranking = await asyncio.to_thread(embeddings.rerank, query, docs, top_k=top_k)
    rerank_latency_ms = round((time.perf_counter() - tr0) * 1000, 1)
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    results = [
        SearchResult(chunk=candidates[idx].chunk, score=score,
                     retrieval_mode="hybrid_rerank",
                     latency_ms=latency_ms, rerank_latency_ms=rerank_latency_ms)
        for idx, score in ranking
    ]
    floor = MIN_RERANK_SCORE if min_rerank_score is None else min_rerank_score
    return _apply_score_floor(results, floor)


# --------------------------------------------------------------------------- #
# CLI debugging tool
# --------------------------------------------------------------------------- #


def _print_results(query: str, product: str, mode: str, results: list[SearchResult]) -> None:
    print(f"\nquery   : {query!r}")
    print(f"product : {product}   mode: {mode}   results: {len(results)}")
    if results:
        top = results[0]
        timing = f"{top.latency_ms} ms total"
        if top.rerank_latency_ms is not None:
            timing += f" ({top.rerank_latency_ms} ms rerank)"
        print(f"latency : {timing}")
    print("-" * 72)
    for i, r in enumerate(results, 1):
        path = " > ".join(r.chunk.section_path) or "(no section)"
        snippet = " ".join(r.chunk.text.split())[:140]
        print(f"{i:>2}. [{r.score:.4f}] {path}")
        print(f"    {snippet}")
        print(f"    {r.chunk.source_url}")


class _Scored:
    """Minimal stand-in with a .score, for the offline floor self-test."""

    def __init__(self, score: float):
        self.score = score


def _selftest() -> None:
    """Offline check of the score-floor logic (no Qdrant/Voyage needed)."""
    res = [_Scored(0.5), _Scored(0.03), _Scored(0.2)]
    assert [r.score for r in _apply_score_floor(res, 0.1)] == [0.5, 0.2], "floor kept wrong rows"
    assert len(_apply_score_floor(res, 0.0)) == 3, "floor<=0 should disable filtering"
    assert _apply_score_floor([_Scored(0.03)], 0.1) == [], "all-below-floor should be empty (-> abstain)"
    print(f"OK: score floor (env default MIN_RERANK_SCORE={MIN_RERANK_SCORE})")
    print("ALL RETRIEVAL SELF-TESTS PASSED")


def main() -> None:
    ap = argparse.ArgumentParser(description="Query the hybrid retrieval engine")
    ap.add_argument("--query", help="question to retrieve for (omit with --selftest)")
    ap.add_argument("--product", default="erp", help="erp | epm | oci")
    ap.add_argument(
        "--mode", default="hybrid_rerank",
        choices=["vector_only", "hybrid", "hybrid_rerank"],
    )
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--min-rerank-score", type=float, default=None,
                    help=f"relevance floor for hybrid_rerank (env default {MIN_RERANK_SCORE})")
    ap.add_argument("--selftest", action="store_true", help="run offline floor self-test and exit")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return
    if not args.query:
        ap.error("--query is required (or pass --selftest)")

    # Keep dependency chatter out of the pretty output.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    results = asyncio.run(retrieve(args.query, args.product, args.mode,
                                   top_k=args.top_k, min_rerank_score=args.min_rerank_score))
    _print_results(args.query, args.product, args.mode, results)


if __name__ == "__main__":
    main()
