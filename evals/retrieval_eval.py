"""Retrieval-level evaluation — the headline scorecard.

Compares the three retrieval modes (vector_only / hybrid / hybrid_rerank) directly
on `shared.retrieval.retrieve()`, bypassing the agent and the LLM judge. This
isolates exactly what the modes change — ranking quality — and is near-free and
fast (just embed/search/rerank, no Claude).

For each (question, expected_product) pair it finds the rank of the first
retrieved chunk that matches an expected section keyword, then reports per mode:
recall@k (k = 1,3,5,10) and Mean Reciprocal Rank. MRR is rank-sensitive, so it
separates the modes even when recall@5 saturates.

Run (in the evals container, which has shared/ + Qdrant + SQLite):
    docker compose run --rm evals python -m evals.retrieval_eval
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from pathlib import Path

from shared import db, retrieval

log = logging.getLogger("evals.retrieval")

MODES = ["vector_only", "hybrid", "hybrid_rerank"]
TOP_K = 10
KS = [1, 3, 5, 10]
DATASET = Path(__file__).parent / "dataset.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"


def _first_match_rank(results, keywords: list[str]) -> int | None:
    """1-based rank of the first result whose text/section matches a keyword."""
    lowered = [k.lower() for k in keywords]
    for rank, sr in enumerate(results, 1):
        hay = (" ".join(sr.chunk.section_path) + " " + sr.chunk.text).lower()
        if any(k in hay for k in lowered):
            return rank
    return None


async def evaluate() -> list[dict]:
    # Read-only SQLite (the chunks.db mount is :ro); share it with the retrieval engine.
    retrieval.set_db_connection(db.connect(read_only=True))
    dataset = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    rows: list[dict] = []
    for q in dataset:
        for product in q["expected_products"]:
            for mode in MODES:
                results = await retrieval.retrieve(q["question"], product, mode, top_k=TOP_K)
                rank = _first_match_rank(results, q["expected_section_keywords"])
                rows.append({
                    "question_id": q["id"], "product": product, "mode": mode,
                    "rank": rank, "reciprocal_rank": (1.0 / rank) if rank else 0.0,
                })
            log.info("%s/%s ranks: %s", q["id"], product,
                     {r["mode"]: r["rank"] for r in rows[-len(MODES):]})
    return rows


def summarize(rows: list[dict]) -> str:
    header = "| mode | " + " | ".join(f"recall@{k}" for k in KS) + " | MRR | n |"
    lines = [header, "|" + "---|" * (len(KS) + 3)]
    for mode in MODES:
        rs = [r for r in rows if r["mode"] == mode]
        if not rs:
            continue
        n = len(rs)
        cells = []
        for k in KS:
            recall = sum(1 for r in rs if r["rank"] and r["rank"] <= k) / n
            cells.append(f"{recall:.0%}")
        mrr = statistics.mean(r["reciprocal_rank"] for r in rs)
        lines.append(f"| {mode} | " + " | ".join(cells) + f" | {mrr:.3f} | {n} |")
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    rows = asyncio.run(evaluate())
    summary = summarize(rows)

    (RESULTS_DIR / f"{ts}_retrieval.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )
    (RESULTS_DIR / f"{ts}_retrieval.md").write_text(
        f"# Retrieval scorecard — {ts}\n\n"
        f"{len(rows)} (question, product, mode) evaluations across {len(MODES)} modes, "
        f"top_k={TOP_K}. Rank = position of the first chunk matching an expected "
        f"section keyword; recall@k and Mean Reciprocal Rank per mode.\n\n{summary}\n"
    )
    print("\n" + summary)
    print(f"\nwrote {ts}_retrieval.md")


if __name__ == "__main__":
    main()
