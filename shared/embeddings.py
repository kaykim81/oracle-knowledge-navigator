"""Voyage AI embedding client wrapper.

Thin layer over the official ``voyageai`` SDK that:

- embeds in batches (``BATCH_SIZE`` = 128 texts per request),
- retries transient failures (429 rate limits, 5xx, timeouts) with exponential
  backoff + jitter, while letting non-retryable errors (auth, bad request)
  propagate immediately,
- pins the output dimension to 1024 to match the Qdrant collections, and
- logs per-batch progress.

Used by ingestion (``input_type="document"``) and by retrieval at query time
(``input_type="query"`` — Voyage embeds queries and documents differently).

Smoke test::

    python -m shared.embeddings           # offline: batching + retry logic
    python -m shared.embeddings --live     # one real API call (needs VOYAGE_API_KEY)
"""

from __future__ import annotations

import logging
import random
import time
from typing import Iterable, Iterator, Literal, Sequence

import voyageai
from voyageai import error as voyage_error

log = logging.getLogger(__name__)

DEFAULT_MODEL = "voyage-3-large"
RERANK_MODEL = "rerank-2"
EMBEDDING_DIM = 1024
BATCH_SIZE = 128

# Transient failures worth retrying. Auth / invalid-request errors are NOT here
# and so propagate immediately instead of being retried pointlessly.
_RETRYABLE = (
    voyage_error.RateLimitError,
    voyage_error.ServerError,
    voyage_error.ServiceUnavailableError,
    voyage_error.APIConnectionError,
    voyage_error.Timeout,
)

InputType = Literal["document", "query"]

_client: voyageai.Client | None = None


def _get_client() -> voyageai.Client:
    """Lazily build a module-level client (reads VOYAGE_API_KEY from the env)."""
    global _client
    if _client is None:
        _client = voyageai.Client()
    return _client


def _batched(items: Sequence[str], n: int) -> Iterator[Sequence[str]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _embed_with_retry(
    client: voyageai.Client,
    texts: Sequence[str],
    *,
    model: str,
    input_type: InputType,
    max_retries: int,
    base_delay: float,
):
    attempt = 0
    while True:
        try:
            return client.embed(
                list(texts),
                model=model,
                input_type=input_type,
                output_dimension=EMBEDDING_DIM,
            )
        except _RETRYABLE as exc:
            attempt += 1
            if attempt > max_retries:
                log.error("Voyage embed failed after %d retries: %s", max_retries, exc)
                raise
            # exponential backoff with full jitter
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, base_delay)
            log.warning(
                "Voyage %s (attempt %d/%d); backing off %.1fs",
                type(exc).__name__,
                attempt,
                max_retries,
                delay,
            )
            time.sleep(delay)


def embed_texts(
    texts: Sequence[str],
    *,
    input_type: InputType = "document",
    model: str = DEFAULT_MODEL,
    batch_size: int = BATCH_SIZE,
    max_retries: int = 6,
    base_delay: float = 1.0,
    client: voyageai.Client | None = None,
) -> list[list[float]]:
    """Embed a list of texts, returning one 1024-d vector per input (in order)."""
    if not texts:
        return []
    client = client or _get_client()
    batches = list(_batched(texts, batch_size))
    out: list[list[float]] = []
    for i, batch in enumerate(batches, 1):
        result = _embed_with_retry(
            client,
            batch,
            model=model,
            input_type=input_type,
            max_retries=max_retries,
            base_delay=base_delay,
        )
        out.extend(result.embeddings)
        log.info(
            "Embedded batch %d/%d (%d/%d texts, %s tokens)",
            i,
            len(batches),
            len(out),
            len(texts),
            getattr(result, "total_tokens", "?"),
        )
    if out and len(out[0]) != EMBEDDING_DIM:
        raise ValueError(
            f"Unexpected embedding dimension {len(out[0])} (expected {EMBEDDING_DIM})"
        )
    return out


def embed_query(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    client: voyageai.Client | None = None,
) -> list[float]:
    """Embed a single query string (uses input_type='query')."""
    return embed_texts([text], input_type="query", model=model, client=client)[0]


def rerank(
    query: str,
    documents: Sequence[str],
    *,
    model: str = RERANK_MODEL,
    top_k: int | None = None,
    max_retries: int = 6,
    base_delay: float = 1.0,
    client: voyageai.Client | None = None,
) -> list[tuple[int, float]]:
    """Rerank documents by relevance to the query.

    Returns ``[(original_index, relevance_score)]`` ordered best-first, limited
    to ``top_k`` if given. Same transient-error backoff as embedding.
    """
    if not documents:
        return []
    client = client or _get_client()
    attempt = 0
    while True:
        try:
            result = client.rerank(query, list(documents), model=model, top_k=top_k)
            break
        except _RETRYABLE as exc:
            attempt += 1
            if attempt > max_retries:
                log.error("Voyage rerank failed after %d retries: %s", max_retries, exc)
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, base_delay)
            log.warning(
                "Voyage rerank %s (attempt %d/%d); backing off %.1fs",
                type(exc).__name__, attempt, max_retries, delay,
            )
            time.sleep(delay)
    return [(r.index, r.relevance_score) for r in result.results]


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #


def _smoke_test(live: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # --- batching math (offline) --------------------------------------------
    sizes = [len(b) for b in _batched(["x"] * 300, 128)]
    assert sizes == [128, 128, 44], sizes
    assert list(_batched([], 128)) == []
    assert embed_texts([]) == []
    print(f"OK: batching splits 300 -> {sizes}; empty input -> []")

    # --- batching + dim with a fake client (offline) ------------------------
    class _Resp:
        def __init__(self, n):
            self.embeddings = [[0.0] * EMBEDDING_DIM for _ in range(n)]
            self.total_tokens = n

    class _FakeClient:
        def __init__(self):
            self.calls = 0

        def embed(self, texts, **kwargs):
            self.calls += 1
            return _Resp(len(texts))

    fake = _FakeClient()
    vecs = embed_texts(["t"] * 300, client=fake)
    assert len(vecs) == 300 and all(len(v) == EMBEDDING_DIM for v in vecs)
    assert fake.calls == 3, f"expected 3 batch calls, got {fake.calls}"
    print(f"OK: 300 texts -> 300 vectors of dim {EMBEDDING_DIM} in {fake.calls} batch calls")

    # --- retry on transient errors (offline, no real sleeping) --------------
    class _FlakyClient:
        def __init__(self, fail_times):
            self.fail_times = fail_times
            self.calls = 0

        def embed(self, texts, **kwargs):
            self.calls += 1
            if self.calls <= self.fail_times:
                raise voyage_error.RateLimitError("simulated 429")
            return _Resp(len(texts))

    flaky = _FlakyClient(fail_times=2)
    vecs = embed_texts(["t", "t"], client=flaky, base_delay=0.0, max_retries=5)
    assert len(vecs) == 2 and flaky.calls == 3
    print(f"OK: retried through 2 rate-limit errors, succeeded on call {flaky.calls}")

    # retries are bounded: a permanently failing client eventually raises
    always = _FlakyClient(fail_times=999)
    try:
        embed_texts(["t"], client=always, base_delay=0.0, max_retries=3)
    except voyage_error.RateLimitError:
        print(f"OK: gave up after {always.calls} attempts (1 + 3 retries)")
        assert always.calls == 4, always.calls
    else:
        raise AssertionError("expected RateLimitError after exhausting retries")

    # --- optional live call --------------------------------------------------
    if live:
        print("\n--- LIVE: calling Voyage API ---")
        vecs = embed_texts(["hello world", "general ledger journal entry"])
        assert len(vecs) == 2 and len(vecs[0]) == EMBEDDING_DIM
        q = embed_query("how do I reverse a journal?")
        assert len(q) == EMBEDDING_DIM
        print(f"OK: live embed -> {len(vecs)} doc vectors + 1 query vector, dim {len(q)}")
    else:
        print("\n(skipping live API test; pass --live to enable)")

    print("\nALL EMBEDDINGS SMOKE TESTS PASSED")


if __name__ == "__main__":
    import sys

    _smoke_test(live="--live" in sys.argv)
