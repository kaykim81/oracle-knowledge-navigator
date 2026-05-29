"""Eval runner — the killer-slide generator.

For every question in the dataset, queries the orchestrator once per retrieval
mode (vector_only / hybrid / hybrid_rerank), records routing + retrieval +
latency, judges the answers (LLM-as-judge, Batches API), then computes per-mode
metrics and writes a markdown comparison table.

Outputs (under evals/results/):
- {ts}.jsonl         — one row per (question, mode), with trace + scores
- {ts}_summary.md    — the comparison table (committed; the headline artifact)

Run: ``python -m evals.runner``   (needs the orchestrator reachable + ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from pathlib import Path

import requests

from evals import judge

log = logging.getLogger("evals.runner")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
MODES = ["vector_only", "hybrid", "hybrid_rerank"]
CATEGORIES = ["single", "cross", "adversarial"]
DATASET = Path(__file__).parent / "dataset.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"
QUERY_TIMEOUT = 300


def _query(question: str, mode: str) -> dict:
    resp = requests.post(
        f"{ORCHESTRATOR_URL}/query",
        json={"question": question, "retrieval_mode": mode},
        timeout=QUERY_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _chunks_from_trace(trace: list[dict]) -> list[dict]:
    """Flatten search_docs chunk metadata across the trace (metadata only)."""
    chunks = []
    for step in trace:
        if step.get("tool") == "search_docs" and isinstance(step.get("results"), list):
            for c in step["results"]:
                if isinstance(c, dict) and "snippet" in c:
                    chunks.append({
                        "section_path": c.get("section_path", []),
                        "source_url": c.get("source_url", ""),
                        "score": c.get("score"),
                        "snippet": c.get("snippet", ""),
                    })
    return chunks


def _category(q: dict) -> str:
    if q["id"].startswith("adv-"):
        return "adversarial"
    if len(q["expected_products"]) > 1:
        return "cross"
    return "single"


def _recall_hit(chunks: list[dict], keywords: list[str]) -> bool:
    """True if any retrieved chunk (section path + snippet) contains a keyword."""
    hay = " ".join(
        " ".join(c.get("section_path") or []) + " " + (c.get("snippet") or "")
        for c in chunks
    ).lower()
    return any(k.lower() in hay for k in keywords)


def run() -> list[dict]:
    dataset = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    limit = os.getenv("EVAL_LIMIT")
    if limit:
        dataset = dataset[: int(limit)]
        log.info("EVAL_LIMIT=%s -> running %d questions", limit, len(dataset))
    results: list[dict] = []
    for q in dataset:
        for mode in MODES:
            try:
                data = _query(q["question"], mode)
            except requests.RequestException as exc:
                log.error("query failed %s/%s: %s", q["id"], mode, exc)
                continue
            trace = data.get("trace", [])
            products = sorted({s["server"] for s in trace})
            chunks = _chunks_from_trace(trace)
            row = {
                "question_id": q["id"], "question": q["question"], "mode": mode,
                "category": _category(q),
                "expected_products": q["expected_products"], "products_called": products,
                "routing_correct": set(products) == set(q["expected_products"]),
                "expected_section_keywords": q["expected_section_keywords"],
                "recall_hit": _recall_hit(chunks, q["expected_section_keywords"]),
                "num_chunks": len(chunks), "retrieved_chunks": chunks,
                "answer": data.get("answer", ""), "latency_ms": data.get("latency_ms", 0),
            }
            results.append(row)
            log.info("%s / %-13s routed=%s recall=%s (%.0f ms)",
                     q["id"], mode, products, row["recall_hit"], row["latency_ms"])
    return results


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, int(round((p / 100) * len(s))) - 1)
    return s[min(k, len(s) - 1)]


def _mode_table(rows: list[dict]) -> str:
    header = ("| mode | routing acc | retrieval recall | quality (mean) | correctness | "
              "groundedness | citation | latency p50 | p95 | n |")
    lines = [header, "|" + "---|" * 10]
    for mode in MODES:
        rs = [r for r in rows if r["mode"] == mode]
        if not rs:
            continue
        n = len(rs)
        routing = sum(r["routing_correct"] for r in rs) / n
        recall = sum(r["recall_hit"] for r in rs) / n
        judged = [r["judge"] for r in rs if r.get("judge")]

        def _avg(field: str) -> float:
            vals = [j[field] for j in judged if j.get(field)]
            return statistics.mean(vals) if vals else 0.0

        corr, ground, cite = _avg("correctness"), _avg("groundedness"), _avg("citation_quality")
        quality = statistics.mean([v for v in (corr, ground, cite) if v]) if judged else 0.0
        lat = [r["latency_ms"] for r in rs]
        lines.append(
            f"| {mode} | {routing:.0%} | {recall:.0%} | {quality:.2f} | {corr:.2f} | "
            f"{ground:.2f} | {cite:.2f} | {_percentile(lat,50):.0f} ms | "
            f"{_percentile(lat,95):.0f} ms | {n} |"
        )
    return "\n".join(lines)


def summarize(results: list[dict]) -> str:
    sections = ["## Overall\n\n" + _mode_table(results)]
    for cat in CATEGORIES:
        rs = [r for r in results if r.get("category") == cat]
        if rs:
            sections.append(f"## {cat} ({len(rs)//len(MODES)} questions)\n\n" + _mode_table(rs))
    return "\n\n".join(sections)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    results = run()
    log.info("ran %d (question, mode) rows; judging…", len(results))
    try:
        for row, score in zip(results, judge.judge_rows(results)):
            row["judge"] = score
    except Exception as exc:  # judging is best-effort; keep the raw results either way
        log.error("judging failed (%s); writing results without scores", exc)

    out_jsonl = RESULTS_DIR / f"{ts}.jsonl"
    with out_jsonl.open("w") as f:
        for row in results:
            f.write(json.dumps(row) + "\n")

    summary = summarize(results)
    out_md = RESULTS_DIR / f"{ts}_summary.md"
    out_md.write_text(
        f"# Eval summary — {ts}\n\n"
        f"{len(results)} (question, mode) rows across {len(MODES)} retrieval modes "
        f"on {len(results)//len(MODES)} questions.\n\n{summary}\n"
    )
    print("\n" + summary)
    print(f"\nwrote {out_jsonl.name} and {out_md.name}")


if __name__ == "__main__":
    main()
