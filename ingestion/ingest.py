"""Ingestion orchestrator: fetched files -> chunks -> embeddings -> stores.

Reads each product's ``_fetched.json`` (written by ``fetch_docs.py``), parses
and chunks every file (HTML via the structure-aware HTML chunker, PDF via the
font-size PDF chunker), embeds the chunk text with Voyage, and writes to BOTH
stores: SQLite/FTS5 (BM25) and Qdrant (vectors).

Idempotent: chunk IDs are deterministic, so re-running the same sources upserts
in place rather than duplicating.

Usage::

    python -m ingestion.ingest --dry-run                 # chunk + count only (no cost)
    python -m ingestion.ingest --product oci --limit-docs 1 \\
        --db-path :memory: --qdrant-location :memory:     # tiny end-to-end test
    python -m ingestion.ingest                            # full run (writes to real stores)
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from shared import db, embeddings, qdrant_store
from shared.chunking import chunk_html, chunk_pdf
from shared.models import Chunk

log = logging.getLogger("ingest")

SOURCES_DIR = Path(__file__).parent / "sources"
INDEX_PATH = SOURCES_DIR / "_index.json"


def load_records(product: str) -> list[dict]:
    path = SOURCES_DIR / product / "_fetched.json"
    if not path.exists():
        log.warning("no _fetched.json for %s (run fetch_docs first) -> skipping", product)
        return []
    return json.loads(path.read_text())


def chunks_for_record(record: dict) -> list[Chunk]:
    path = SOURCES_DIR / record["file"]
    common = dict(product=record["product"], doc_id=record["doc_id"],
                  source_url=record["source_url"])
    if record["format"] == "pdf":
        return chunk_pdf(str(path), **common)
    if record["format"] == "html":
        return chunk_html(path.read_text(encoding="utf-8"), **common)
    log.warning("unknown format %r for %s", record["format"], record["doc_id"])
    return []


def collect_chunks(products: list[str], *, limit_docs: int = 0) -> list[Chunk]:
    chunks: list[Chunk] = []
    for product in products:
        records = load_records(product)
        if limit_docs:
            records = records[:limit_docs]
        before = len(chunks)
        for record in records:
            try:
                chunks.extend(chunks_for_record(record))
            except Exception as exc:  # resilient: one bad file shouldn't abort
                log.error("failed to chunk %s: %s", record.get("doc_id"), exc)
        log.info("%s: %d files -> %d chunks", product, len(records), len(chunks) - before)
    return chunks


def ingest(
    products: list[str],
    *,
    db_path: str,
    qdrant_location: str | None,
    qdrant_url: str | None,
    limit_docs: int = 0,
    recreate: bool = False,
    dry_run: bool = False,
) -> None:
    chunks = collect_chunks(products, limit_docs=limit_docs)
    log.info("total chunks: %d", len(chunks))
    if not chunks:
        return

    if dry_run:
        per_product: dict[str, int] = {}
        for c in chunks:
            per_product[c.product] = per_product.get(c.product, 0) + 1
        log.info("DRY RUN (no embed/write). chunks per product: %s", per_product)
        return

    # Embed (batched, with retry/backoff inside the wrapper).
    vectors = embeddings.embed_texts([c.text for c in chunks], input_type="document")

    # Write to SQLite (BM25) and Qdrant (vectors).
    conn = db.connect(db_path)
    db.init_db(conn)
    db.upsert_chunks(conn, chunks)

    client = qdrant_store.get_client(location=qdrant_location, url=qdrant_url)
    qdrant_store.init_collections(client, recreate=recreate)
    qdrant_store.upsert_chunks(client, chunks, vectors)

    # Report counts from both stores.
    sqlite_counts = db.count_by_product(conn)
    log.info("=== counts ===")
    for product in products:
        q = qdrant_store.count(client, product)
        s = sqlite_counts.get(product, 0)
        log.info("  %-4s  sqlite=%-6d qdrant=%-6d %s", product, s, q,
                 "OK" if s == q else "MISMATCH")
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest fetched docs into SQLite + Qdrant")
    ap.add_argument("--product", default="all", help="erp | epm | oci | all")
    ap.add_argument("--db-path", default=str(db.DB_PATH), help="SQLite path or :memory:")
    ap.add_argument("--qdrant-location", default=None,
                    help="local Qdrant location (e.g. :memory: or a path); overrides url/host")
    ap.add_argument("--qdrant-url", default=None, help="Qdrant URL (else env / qdrant:6333)")
    ap.add_argument("--limit-docs", type=int, default=0, help="cap docs per product (testing)")
    ap.add_argument("--recreate", action="store_true", help="drop & recreate Qdrant collections")
    ap.add_argument("--dry-run", action="store_true", help="chunk + count only; no embed/write")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    index = json.loads(INDEX_PATH.read_text())
    products = list(index["products"]) if args.product == "all" else [args.product]
    ingest(products, db_path=args.db_path, qdrant_location=args.qdrant_location,
           qdrant_url=args.qdrant_url, limit_docs=args.limit_docs,
           recreate=args.recreate, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
