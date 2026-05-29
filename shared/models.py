"""Core data models shared across the stack.

Three models flow through the whole pipeline:

- ``Document`` — a whole source document (one Oracle guide, or one crawled HTML
  page) before chunking.
- ``Chunk`` — a single retrievable unit: a slice of a document with its heading
  path preserved. This is what gets embedded and stored in Qdrant + SQLite.
- ``SearchResult`` — a ``Chunk`` paired with the score and the retrieval mode
  that produced it. Returned by ``shared.retrieval`` (Phase 2).

Run the smoke test with::

    python -m shared.models
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Product lines, matching the keys in ingestion/sources/_index.json and the
# Qdrant collection prefixes (erp_docs, epm_docs, oci_docs).
Product = Literal["erp", "epm", "oci"]

# Retrieval modes, matching shared.retrieval.retrieve() (Phase 2) and the three
# columns of the eval scorecard (Phase 7).
RetrievalMode = Literal["vector_only", "hybrid", "hybrid_rerank"]

# Fixed namespace so chunk IDs are deterministic: re-ingesting the same source
# produces the same IDs, which makes the Qdrant/SQLite writes idempotent
# (Phase 1, step 9) instead of duplicating points. Qdrant requires point IDs to
# be an unsigned int or a UUID string; uuid5 gives us a stable UUID string.
_CHUNK_ID_NAMESPACE = uuid.UUID("1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed")


class Document(BaseModel):
    """A whole source document, pre-chunking."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    product: Product
    title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    full_text: str = Field(min_length=1)


class Chunk(BaseModel):
    """A single retrievable unit of a document.

    ``section_path`` preserves the heading hierarchy the chunk came from, e.g.
    ``["General Ledger", "Journal Entries", "Reversing Journals"]`` — used both
    for richer retrieval context and for citation display in the UI.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    product: Product
    doc_id: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    section_path: list[str] = Field(default_factory=list)
    source_url: str = Field(min_length=1)

    @staticmethod
    def make_id(doc_id: str, chunk_index: int) -> str:
        """Deterministic chunk ID from its document and position.

        Stable across runs, so ingestion can upsert idempotently.
        """
        return str(uuid.uuid5(_CHUNK_ID_NAMESPACE, f"{doc_id}:{chunk_index}"))

    @classmethod
    def create(
        cls,
        *,
        product: Product,
        doc_id: str,
        chunk_index: int,
        text: str,
        source_url: str,
        section_path: list[str] | None = None,
    ) -> "Chunk":
        """Build a Chunk with a deterministic ``id`` derived from its identity."""
        return cls(
            id=cls.make_id(doc_id, chunk_index),
            product=product,
            doc_id=doc_id,
            chunk_index=chunk_index,
            text=text,
            section_path=section_path or [],
            source_url=source_url,
        )


class SearchResult(BaseModel):
    """A retrieved chunk with its score and the mode that produced it."""

    model_config = ConfigDict(extra="forbid")

    chunk: Chunk
    score: float
    retrieval_mode: RetrievalMode


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #


def _smoke_test() -> None:
    from pydantic import ValidationError

    # --- construction --------------------------------------------------------
    doc = Document(
        id="erp-general-ledger",
        product="erp",
        title="Using General Ledger",
        source_url="https://docs.oracle.com/.../using-general-ledger.pdf",
        full_text="Journal entries record financial transactions...",
    )

    chunk = Chunk.create(
        product="erp",
        doc_id=doc.id,
        chunk_index=0,
        text="A reversing journal reverses the amounts of an existing journal.",
        source_url=doc.source_url,
        section_path=["General Ledger", "Journal Entries", "Reversing Journals"],
    )

    result = SearchResult(chunk=chunk, score=0.873, retrieval_mode="hybrid_rerank")

    print("Document:    ", doc.model_dump_json())
    print("Chunk:       ", chunk.model_dump_json())
    print("SearchResult:", result.model_dump_json())

    # --- deterministic IDs ---------------------------------------------------
    assert Chunk.make_id("erp-general-ledger", 0) == chunk.id, "id not deterministic"
    assert Chunk.make_id("erp-general-ledger", 1) != chunk.id, "id collision across index"
    assert Chunk.make_id("erp-payables", 0) != chunk.id, "id collision across doc"
    print("OK: chunk IDs are deterministic and well-separated")

    # --- round-trip serialization -------------------------------------------
    assert Chunk.model_validate_json(chunk.model_dump_json()) == chunk
    assert SearchResult.model_validate_json(result.model_dump_json()) == result
    print("OK: JSON round-trip is lossless")

    # --- validation guards (each must raise) --------------------------------
    def must_raise(label: str, fn) -> None:
        try:
            fn()
        except ValidationError:
            print(f"OK: rejected {label}")
        else:
            raise AssertionError(f"expected ValidationError for {label}")

    must_raise("invalid product", lambda: Chunk.create(
        product="hcm", doc_id="d", chunk_index=0, text="x", source_url="u"))  # type: ignore[arg-type]
    must_raise("negative chunk_index", lambda: Chunk.create(
        product="erp", doc_id="d", chunk_index=-1, text="x", source_url="u"))
    must_raise("empty text", lambda: Chunk.create(
        product="erp", doc_id="d", chunk_index=0, text="", source_url="u"))
    must_raise("unknown extra field", lambda: SearchResult(
        chunk=chunk, score=0.5, retrieval_mode="hybrid", oops=1))  # type: ignore[call-arg]
    must_raise("bad retrieval_mode", lambda: SearchResult(
        chunk=chunk, score=0.5, retrieval_mode="magic"))  # type: ignore[arg-type]

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    _smoke_test()
