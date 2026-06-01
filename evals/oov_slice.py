"""OOV recall-rescue slice — the regime where dense retrieval genuinely fails.

A standalone analysis (deliberately *not* part of the balanced 15/15/15/15 eval
dataset): minimal-context lookups of ultra-rare exact identifiers (df=2 in the
corpus) that voyage-3-large can't localize, so the BM25 leg must carry recall.

This isolates the one regime the clean-query retrieval_eval can't surface: there,
``vector_only`` recall@10 is ~100% (the chunk is always in the pool, just
mis-ranked) so fusion only ever *re-ranks*. Here vector genuinely *misses* on
recall, and the keyword leg rescues it — hybrid winning outright, the textbook case.

Tokens were confirmed vector-hostile by probing (``vector_only`` rank = None or
deep) and verified to be genuine, answerable domain terms (not incidental noise).

Run::

    sudo docker compose run --rm --build -T evals python -m evals.oov_slice

``HYBRID_FUSION`` (rrf|weighted|adaptive) toggles the ``hybrid`` mode's fusion, so
this doubles as an A/B of *which* hybrid recovers OOV recall (see the module's
note: the regex adaptive router gates BM25 off for acronym queries, so a
query-agnostic rrf/weighted hybrid is the one that rescues every OOV token).
"""

from __future__ import annotations

import asyncio
import statistics

from shared import db, retrieval

# (query, product, expected_token) — minimal-context exact-identifier lookups whose
# token sits in only ~2 chunks. Confirmed vector-hostile; each token verified to be
# a real domain term described in its chunk.
SLICE = [
    ("XCC flexfield code", "epm", "xcc"),
    ("OFS_Rollup rule", "epm", "ofs_rollup"),
    ("OEP_Original version member", "epm", "oep_original"),
    ("OWP_Salary smart list", "epm", "owp_salary"),
    ("BUDGET_VERSION_ID column", "erp", "budget_version_id"),
    ("REFERENCE1 column in GL_INTERFACE", "erp", "reference1"),
    ("CCSP program", "oci", "ccsp"),
    ("KVM guest image format", "oci", "kvm"),
]
MODES = ["keyword_only", "vector_only", "hybrid", "hybrid_rerank"]
KS = [1, 3, 10]


def _rank(results, kw: str) -> int | None:
    """1-based rank of the first result whose section path or text contains kw."""
    for i, r in enumerate(results, 1):
        if kw in (" ".join(r.chunk.section_path) + " " + r.chunk.text).lower():
            return i
    return None


async def evaluate() -> dict[str, list[int | None]]:
    retrieval.set_db_connection(db.connect(read_only=True))
    ranks: dict[str, list[int | None]] = {m: [] for m in MODES}
    for query, product, kw in SLICE:
        for mode in MODES:
            res = await retrieval.retrieve(query, product, mode, top_k=10)
            ranks[mode].append(_rank(res, kw))
    return ranks


def report(ranks: dict[str, list[int | None]]) -> None:
    n = len(SLICE)
    print(f"OOV recall-rescue slice — n={n} minimal-context exact-identifier lookups")
    print(
        f"hybrid fusion: {retrieval.HYBRID_FUSION} "
        f"(alpha={retrieval.HYBRID_ALPHA}, lex={retrieval.HYBRID_ALPHA_LEXICAL}, "
        f"sem={retrieval.HYBRID_ALPHA_SEMANTIC})\n"
    )
    hdr = f"{'mode':14}" + "".join(f"{'recall@'+str(k):>10}" for k in KS) + f"{'MRR':>8}{'misses':>8}"
    print(hdr)
    print("-" * len(hdr))
    for mode in MODES:
        rs = ranks[mode]
        cells = "".join(f"{sum(1 for x in rs if x and x <= k) / n:>10.0%}" for k in KS)
        mrr = statistics.mean((1.0 / x if x else 0.0) for x in rs)
        misses = sum(1 for x in rs if x is None)
        print(f"{mode:14}{cells}{mrr:>8.3f}{misses:>8}")

    vec_miss = [i for i, x in enumerate(ranks["vector_only"]) if x is None]
    kw_saves = sum(1 for i in vec_miss if ranks["keyword_only"][i] is not None)
    hyb_saves = sum(1 for i in vec_miss if ranks["hybrid"][i] is not None)
    print(
        f"\ncomplementarity: vector misses {len(vec_miss)}/{n}; of those, "
        f"keyword rescues {kw_saves}, hybrid rescues {hyb_saves}"
    )

    print("\nper-query rank (None = not retrieved in top-10):")
    print(f"  {'token':22}" + "".join(f"{m[:4]:>7}" for m in MODES))
    for i, (_q, _p, kw) in enumerate(SLICE):
        print(f"  {kw:22}" + "".join(f"{str(ranks[m][i]):>7}" for m in MODES))


def main() -> None:
    report(asyncio.run(evaluate()))


if __name__ == "__main__":
    main()
