"""Retrieval-level evaluation — the headline scorecard.

Compares the three retrieval modes (vector_only / hybrid / hybrid_rerank) directly
on `shared.retrieval.retrieve()`, bypassing the agent and the LLM judge. This
isolates exactly what the modes change — ranking quality — and is near-free and
fast (just embed/search/rerank, no Claude).

For each (question, expected_product) pair it finds the rank of the first
retrieved chunk matching an expected section keyword, under two relevance bars:

- TEXT: the keyword appears anywhere in the chunk (section path or body) — lenient.
- SECTION: the keyword appears in the chunk's section path — strict ("did
  retrieval surface a chunk from the right section?").

Reports per mode: recall@k (k = 1,3,5,10) and Mean Reciprocal Rank, under each
bar. MRR is rank-sensitive, so it separates modes even when recall@5 saturates.

Run (in the evals container, which has shared/ + Qdrant + SQLite):
    docker compose run --rm evals python -m evals.retrieval_eval
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import time
from pathlib import Path

from shared import db, retrieval

log = logging.getLogger("evals.retrieval")

MODES = ["vector_only", "hybrid", "hybrid_rerank"]
TOP_K = 10
KS = [1, 3, 5, 10]
BARS = ["text", "section"]
DATASET = Path(__file__).parent / "dataset.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"


CATEGORIES = ["single", "cross", "adversarial"]


def _category(q: dict) -> str:
    if q["id"].startswith("adv-"):
        return "adversarial"
    if len(q["expected_products"]) > 1:
        return "cross"
    return "single"


def _first_match_rank(results, keywords: list[str], field: str) -> int | None:
    """1-based rank of the first result matching a keyword under the given bar.

    field="text": match section path OR body. field="section": match section path only.
    """
    lowered = [k.lower() for k in keywords]
    for rank, sr in enumerate(results, 1):
        section = " ".join(sr.chunk.section_path).lower()
        hay = section if field == "section" else section + " " + sr.chunk.text.lower()
        if any(k in hay for k in lowered):
            return rank
    return None


async def evaluate() -> list[dict]:
    # Read-only SQLite (the chunks.db mount is :ro); share it with the retrieval engine.
    retrieval.set_db_connection(db.connect(read_only=True))
    dataset = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    category = os.getenv("EVAL_CATEGORY")
    if category:
        dataset = [q for q in dataset if _category(q) == category]
        log.info("EVAL_CATEGORY=%s -> running %d questions", category, len(dataset))
    rows: list[dict] = []
    for q in dataset:
        for product in q["expected_products"]:
            for mode in MODES:
                results = await retrieval.retrieve(q["question"], product, mode, top_k=TOP_K)
                row = {"question_id": q["id"], "category": _category(q),
                       "product": product, "mode": mode}
                for bar in BARS:
                    rank = _first_match_rank(results, q["expected_section_keywords"], bar)
                    row[f"rank_{bar}"] = rank
                    row[f"rr_{bar}"] = (1.0 / rank) if rank else 0.0
                rows.append(row)
            log.info("%s/%s text-ranks: %s | section-ranks: %s", q["id"], product,
                     {r["mode"]: r["rank_text"] for r in rows[-len(MODES):]},
                     {r["mode"]: r["rank_section"] for r in rows[-len(MODES):]})
    return rows


def summarize(rows: list[dict], bar: str) -> str:
    header = "| mode | " + " | ".join(f"recall@{k}" for k in KS) + " | MRR | n |"
    lines = [header, "|" + "---|" * (len(KS) + 3)]
    for mode in MODES:
        rs = [r for r in rows if r["mode"] == mode]
        if not rs:
            continue
        n = len(rs)
        cells = [
            f"{sum(1 for r in rs if r[f'rank_{bar}'] and r[f'rank_{bar}'] <= k) / n:.0%}"
            for k in KS
        ]
        mrr = statistics.mean(r[f"rr_{bar}"] for r in rs)
        lines.append(f"| {mode} | " + " | ".join(cells) + f" | {mrr:.3f} | {n} |")
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    rows = asyncio.run(evaluate())
    text_table = summarize(rows, "text")
    section_table = summarize(rows, "section")
    per_category = "".join(
        f"### {cat} ({len([r for r in rows if r['category']==cat]) // len(MODES)} questions, "
        f"section bar)\n\n{summarize([r for r in rows if r['category']==cat], 'section')}\n\n"
        for cat in CATEGORIES if any(r["category"] == cat for r in rows)
    )

    (RESULTS_DIR / f"{ts}_retrieval.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )
    (RESULTS_DIR / f"{ts}_retrieval.md").write_text(
        f"# Retrieval scorecard — {ts}\n\n"
        f"{len(rows)} (question, product, mode) evaluations across {len(MODES)} modes, "
        f"top_k={TOP_K}.\n\n"
        f"## Relevance bar: keyword anywhere in chunk (lenient)\n\n{text_table}\n\n"
        f"## Relevance bar: keyword in section path (strict — right section)\n\n{section_table}\n\n"
        f"## Per category (strict section bar)\n\n{per_category}"
    )
    print("\n## TEXT bar (lenient)\n" + text_table)
    print("\n## SECTION bar (strict)\n" + section_table)
    print("\n## PER CATEGORY (section bar)\n" + per_category)
    print(f"wrote {ts}_retrieval.md")


if __name__ == "__main__":
    main()
