"""Qdrant vector store: client, collection init, upsert, and vector search.

One collection per product (``{product}_docs``), each holding 1024-d vectors
(voyage-3-large) compared by cosine distance. Per-collection isolation is the
enforcement boundary in the architecture: an MCP server configured for one
product can only ever reach its own collection.

Shared by ingestion (init + upsert, Phase 1) and retrieval (search, Phase 2).

Connection resolution (in order): explicit ``location`` (e.g. ``":memory:"``
for tests) > ``url`` arg > ``QDRANT_URL`` env > ``QDRANT_HOST``/``QDRANT_PORT``
env > default ``qdrant:6333`` (the service name on the internal network).

Smoke test (in-memory, no server needed)::

    python -m shared.qdrant_store
"""

from __future__ import annotations

import os

from qdrant_client import QdrantClient, models

from .models import Chunk, Product

PRODUCTS: tuple[Product, ...] = ("erp", "epm", "oci")
VECTOR_SIZE = 1024
DISTANCE = models.Distance.COSINE

# Payload fields stored per point (everything except the vector). Chunk.id is
# also the Qdrant point id, but we keep it in the payload too for convenience.
_PAYLOAD_FIELDS = ("id", "product", "doc_id", "chunk_index", "text", "section_path", "source_url")


def collection_for(product: Product) -> str:
    return f"{product}_docs"


def get_client(
    *,
    location: str | None = None,
    url: str | None = None,
    host: str | None = None,
    port: int = 6333,
) -> QdrantClient:
    if location is not None:
        return QdrantClient(location=location)
    url = url or os.getenv("QDRANT_URL")
    if url:
        return QdrantClient(url=url)
    host = host or os.getenv("QDRANT_HOST", "qdrant")
    port = int(os.getenv("QDRANT_PORT", port))
    return QdrantClient(host=host, port=port)


def init_collections(client: QdrantClient, *, recreate: bool = False) -> list[str]:
    """Create the three product collections if absent. Returns their names.

    ``recreate=True`` drops and recreates (destructive — wipes vectors).
    """
    created: list[str] = []
    for product in PRODUCTS:
        name = collection_for(product)
        if recreate and client.collection_exists(name):
            client.delete_collection(name)
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=DISTANCE),
            )
        created.append(name)
    return created


def _payload(chunk: Chunk) -> dict:
    data = chunk.model_dump()
    return {k: data[k] for k in _PAYLOAD_FIELDS}


def upsert_chunks(
    client: QdrantClient, chunks: list[Chunk], vectors: list[list[float]]
) -> int:
    """Upsert chunks (with their vectors) into the right per-product collection."""
    if len(chunks) != len(vectors):
        raise ValueError(f"chunks ({len(chunks)}) and vectors ({len(vectors)}) length mismatch")
    by_collection: dict[str, list[models.PointStruct]] = {}
    for chunk, vector in zip(chunks, vectors):
        if len(vector) != VECTOR_SIZE:
            raise ValueError(f"vector dim {len(vector)} != {VECTOR_SIZE}")
        point = models.PointStruct(id=chunk.id, vector=vector, payload=_payload(chunk))
        by_collection.setdefault(collection_for(chunk.product), []).append(point)
    for name, points in by_collection.items():
        client.upsert(collection_name=name, points=points)
    return len(chunks)


def _chunk_from_payload(payload: dict) -> Chunk:
    return Chunk(**{k: payload[k] for k in _PAYLOAD_FIELDS})


def search(
    client: QdrantClient,
    product: Product,
    query_vector: list[float],
    *,
    limit: int = 10,
) -> list[tuple[Chunk, float]]:
    """Vector search within one product's collection. Higher score = better (cosine)."""
    resp = client.query_points(
        collection_name=collection_for(product),
        query=query_vector,
        limit=limit,
        with_payload=True,
    )
    return [(_chunk_from_payload(p.payload), p.score) for p in resp.points]


def count(client: QdrantClient, product: Product) -> int:
    return client.count(collection_for(product), exact=True).count


# --------------------------------------------------------------------------- #
# CLI smoke test (in-memory)
# --------------------------------------------------------------------------- #


def _vec(i: int) -> list[float]:
    """A unit vector with a 1.0 at position i (distinct, cosine-friendly)."""
    v = [0.0] * VECTOR_SIZE
    v[i] = 1.0
    return v


def _smoke_test() -> None:
    client = get_client(location=":memory:")

    names = init_collections(client)
    assert names == ["erp_docs", "epm_docs", "oci_docs"], names
    assert all(client.collection_exists(n) for n in names)
    # idempotent: re-init does not error
    init_collections(client)
    print("OK: created/verified collections", names)

    chunks = [
        Chunk.create(product="erp", doc_id="erp-gl", chunk_index=0,
                     text="reversing journal entry", source_url="https://x/gl",
                     section_path=["General Ledger", "Journals"]),
        Chunk.create(product="erp", doc_id="erp-gl", chunk_index=1,
                     text="supplier invoices", source_url="https://x/gl"),
        Chunk.create(product="oci", doc_id="oci-compute", chunk_index=0,
                     text="compute instance shape", source_url="https://x/oci"),
    ]
    vectors = [_vec(0), _vec(1), _vec(0)]
    assert upsert_chunks(client, chunks, vectors) == 3
    assert count(client, "erp") == 2 and count(client, "oci") == 1 and count(client, "epm") == 0
    print("OK: upserted -> erp=2 oci=1 epm=0")

    # nearest to _vec(0) within erp is chunk 0; payload round-trips to a Chunk
    hits = search(client, "erp", _vec(0), limit=5)
    assert hits[0][0].chunk_index == 0 and hits[0][1] > 0.99
    assert hits[0][0].section_path == ["General Ledger", "Journals"], "section_path lost"
    print(f"OK: vector search top hit chunk_index=0, score={hits[0][1]:.3f}")

    # collection isolation: the oci point never appears in an erp search
    erp_doc_ids = {c.doc_id for c, _ in search(client, "erp", _vec(0), limit=10)}
    assert "oci-compute" not in erp_doc_ids
    print("OK: per-collection isolation holds")

    # idempotent upsert: same ids -> no duplication
    upsert_chunks(client, chunks, vectors)
    assert count(client, "erp") == 2 and count(client, "oci") == 1
    print("OK: idempotent upsert (no duplicate points)")

    print("\nALL QDRANT_STORE SMOKE TESTS PASSED")


if __name__ == "__main__":
    _smoke_test()
