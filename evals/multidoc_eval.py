"""Multi-doc retrieval eval — set-recall@k + nDCG@k via pooled LLM relevance judgments.

The standard retrieval_eval uses a *single-target* metric (recall@1 / MRR against one
keyword-defined chunk). That structurally hides hybrid's real strength — covering a
*set* of relevant docs where vector finds some and keyword finds others. But a credible
multi-doc gold can't be defined cheaply: keyword/section proxies are too coarse (a
section keyword matches a whole subtree). So this uses TREC-style **pooled judgments**:

  1. For each retrieval unit (query x product), pool the top-k from every mode (union).
  2. One LLM call grades each pooled passage 0-3 for relevance to the query.
  3. Gold = passages judged >= 2; compute set-recall@k and graded nDCG@k per mode.

This is the metric that rewards the union-of-relevant-docs benefit — measured, not argued.

Run (dry-run 2 units first, then full)::

    sudo docker compose run --rm --build -T evals python -m evals.multidoc_eval --limit 2
    sudo docker compose run --rm --build -T evals python -m evals.multidoc_eval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import statistics
from pathlib import Path

import anthropic

from evals.judge import MODEL
from shared import db, retrieval

log = logging.getLogger("evals.multidoc_eval")

# Modes pooled AND evaluated (pool from all systems being compared — TREC pooling).
MODES = ["vector_only", "keyword_only", "hybrid", "hybrid_rerank"]
TOP_K = 10
MAX_PASSAGE_CHARS = 400  # truncate each passage in the judge prompt (relevance needs less than groundedness)
REL_THRESHOLD = 2        # judged score >= this counts as a gold (relevant) chunk

DATASET = Path(__file__).parent / "dataset.jsonl"

_SYSTEM = """You judge passage relevance for a search evaluation. For the QUERY, rate how well EACH passage helps answer it, on a 0-3 scale:
3 = directly and fully answers the query
2 = relevant, partially answers it
1 = related topic but does not answer it
0 = irrelevant / off-topic
Return ONLY a JSON object mapping each passage number (as a string) to its integer score, e.g. {"1": 3, "2": 0, "3": 2}. Score EVERY passage; output no prose."""


def _semantic_units(limit: int | None) -> list[tuple[dict, str]]:
    """(question, product) units for the semantic categories (single + cross)."""
    rows = [json.loads(l) for l in DATASET.read_text().splitlines() if l.strip()]
    units: list[tuple[dict, str]] = []
    for q in rows:
        if q.get("tag") == "exact_term" or q["id"].startswith("adv-"):
            continue  # semantic regimes only — multi-doc relevance is real there
        for product in q["expected_products"]:
            units.append((q, product))
    return units[:limit] if limit else units


async def _pool(question: str, product: str) -> tuple[dict[str, object], dict[str, list[str]]]:
    """Return (id->chunk for the pooled union, mode->ranked list of ids)."""
    chunks: dict[str, object] = {}
    rankings: dict[str, list[str]] = {}
    for mode in MODES:
        results = await retrieval.retrieve(question, product, mode, top_k=TOP_K)
        rankings[mode] = [r.chunk.id for r in results]
        for r in results:
            chunks[r.chunk.id] = r.chunk
    return chunks, rankings


def _judge(client, question: str, product: str, chunks: dict[str, object]) -> dict[str, int]:
    """One LLM call grading every pooled passage 0-3. Returns chunk_id -> score."""
    ids = list(chunks)
    passages = "\n".join(
        f"{i + 1}. [{' > '.join(chunks[cid].section_path)}] "
        f"{' '.join(chunks[cid].text.split())[:MAX_PASSAGE_CHARS]}"
        for i, cid in enumerate(ids)
    )
    user = f"QUERY (about Oracle {product.upper()}):\n{question}\n\nPASSAGES:\n{passages}"
    msg = client.messages.create(
        model=MODEL, max_tokens=1024, system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except (ValueError, TypeError):
        obj = {}
    scores: dict[str, int] = {}
    for i, cid in enumerate(ids):
        try:
            scores[cid] = max(0, min(3, int(obj.get(str(i + 1), 0))))
        except (TypeError, ValueError):
            scores[cid] = 0
    return scores


def _dcg(gains: list[int]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def _ndcg(ranked_ids: list[str], judged: dict[str, int], k: int) -> float:
    gains = [judged.get(cid, 0) for cid in ranked_ids[:k]]
    ideal = sorted(judged.values(), reverse=True)[:k]
    idcg = _dcg(ideal)
    return _dcg(gains) / idcg if idcg > 0 else 0.0


def _recall(ranked_ids: list[str], gold: set[str], k: int) -> float | None:
    if not gold:
        return None  # no relevant chunk in the pool -> skip this unit
    return len(set(ranked_ids[:k]) & gold) / len(gold)


def run(limit: int | None) -> None:
    retrieval.set_db_connection(db.connect(read_only=True))
    client = anthropic.Anthropic()
    units = _semantic_units(limit)
    log.info("judging %d (query, product) units, pooling top-%d from %d modes", len(units), TOP_K, len(MODES))

    recalls: dict[str, list[float]] = {m: [] for m in MODES}
    ndcgs: dict[str, list[float]] = {m: [] for m in MODES}
    pool_sizes, gold_sizes = [], []

    for n, (q, product) in enumerate(units, 1):
        chunks, rankings = asyncio.run(_pool(q["question"], product))
        judged = _judge(client, q["question"], product, chunks)
        gold = {cid for cid, s in judged.items() if s >= REL_THRESHOLD}
        pool_sizes.append(len(chunks)); gold_sizes.append(len(gold))
        for m in MODES:
            r = _recall(rankings[m], gold, TOP_K)
            if r is not None:
                recalls[m].append(r)
            ndcgs[m].append(_ndcg(rankings[m], judged, TOP_K))
        log.info("%2d/%2d %-22s/%s pool=%d gold=%d", n, len(units), q["id"][:22], product, len(chunks), len(gold))

    print(f"\nMulti-doc eval — {len(units)} (query, product) units (single + cross), pooled LLM judgments")
    print(f"pool: top-{TOP_K} union of {MODES}; gold = judged >= {REL_THRESHOLD}/3")
    print(f"avg pool size {statistics.mean(pool_sizes):.1f}, avg gold size {statistics.mean(gold_sizes):.1f}\n")
    print(f"{'mode':14}{'set-recall@'+str(TOP_K):>16}{'nDCG@'+str(TOP_K):>12}{'n':>5}")
    print("-" * 47)
    for m in MODES:
        rec = statistics.mean(recalls[m]) if recalls[m] else float("nan")
        nd = statistics.mean(ndcgs[m]) if ndcgs[m] else float("nan")
        print(f"{m:14}{rec:>16.3f}{nd:>12.3f}{len(recalls[m]):>5}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Multi-doc retrieval eval (pooled LLM judgments)")
    ap.add_argument("--limit", type=int, default=None, help="only run the first N units (dry-run / cost control)")
    run(ap.parse_args().limit)


if __name__ == "__main__":
    main()
