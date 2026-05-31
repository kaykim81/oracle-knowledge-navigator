"""SQLite storage for chunks + FTS5 keyword (BM25) index.

The ``chunks`` table mirrors the ``Chunk`` model one-to-one (``section_path`` is
stored as a JSON string since SQLite has no list type). An external-content FTS5
table ``chunks_fts`` indexes ``text`` for BM25 search and is kept in sync with
``chunks`` by triggers, so callers only ever write to ``chunks``.

Upserts key on the deterministic chunk ``id`` (see ``shared.models.Chunk``), so
re-ingesting the same source is idempotent — no duplicate rows.

This pairs with the Qdrant vector store; ``shared.retrieval`` (Phase 2) combines
the BM25 results here with vector results from Qdrant.

Smoke test (in-memory DB, no files touched)::

    python -m shared.db
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .models import Chunk, Document, Product

# data/sqlite/chunks.db under the project root; bind-mounted into containers.
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sqlite" / "chunks.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,
    product      TEXT NOT NULL,
    doc_id       TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    section_path TEXT NOT NULL,   -- JSON-encoded list[str]
    source_url   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_product ON chunks(product);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='rowid'
);

-- Keep the FTS index in sync with the chunks table.
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.rowid, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""

_UPSERT = """
INSERT INTO chunks (id, product, doc_id, chunk_index, text, section_path, source_url)
VALUES (:id, :product, :doc_id, :chunk_index, :text, :section_path, :source_url)
ON CONFLICT(id) DO UPDATE SET
    product=excluded.product,
    doc_id=excluded.doc_id,
    chunk_index=excluded.chunk_index,
    text=excluded.text,
    section_path=excluded.section_path,
    source_url=excluded.source_url;
"""


def connect(db_path: Path | str = DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a connection, creating the parent directory for file-based DBs.

    ``read_only=True`` opens in SQLite read-only mode — required when the DB
    file is on a read-only bind mount (the MCP servers mount chunks.db ``:ro``).
    """
    # check_same_thread=False: retrieval runs BM25 in an asyncio.to_thread worker.
    # Safe here — retrieval only reads; writes (ingestion) are single-threaded.
    if read_only and db_path != ":memory:":
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    else:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def _chunk_to_params(chunk: Chunk) -> dict:
    data = chunk.model_dump()
    data["section_path"] = json.dumps(data["section_path"])
    return data


def upsert_chunks(conn: sqlite3.Connection, chunks: list[Chunk]) -> int:
    """Insert or update chunks by id. Returns the number of rows written."""
    if not chunks:
        return 0
    conn.executemany(_UPSERT, [_chunk_to_params(c) for c in chunks])
    conn.commit()
    return len(chunks)


def _clean_heading(heading: str) -> str:
    """Strip leading section numbering from a heading for display.

    The PDF-sourced corpora (ERP, EPM) carry numbered top-level headings like
    "19 Managing Consolidation Journals" or "2.3 Journals"; the HTML-crawled OCI
    corpus does not. Drop a leading number (optionally dotted) so topic lists and
    document titles read cleanly. If stripping would leave nothing (a heading that
    is *only* a number, e.g. "25D"), keep the original.
    """
    cleaned = re.sub(r"^\d+(\.\d+)*\s+", "", heading).strip()
    return cleaned or heading


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        id=row["id"],
        product=row["product"],
        doc_id=row["doc_id"],
        chunk_index=row["chunk_index"],
        text=row["text"],
        section_path=json.loads(row["section_path"]),
        source_url=row["source_url"],
    )


# Common English words dropped from the BM25 query. OR-ing these in (a, in, how,
# do, ...) makes the keyword leg match almost every chunk, polluting RRF fusion
# and making hybrid underperform pure vector. Filtering them keeps OR's recall
# benefit without the noise.
_STOPWORDS = frozenset("""
a an and are as at be by do does for from how i in into is it its my of on or
that the their to was what when where which who why will with you your me we our
""".split())


def _fts_query(query: str) -> str:
    """Build a safe FTS5 MATCH string: content words quoted, OR-ed together.

    Words are OR-ed (not implicit AND) so natural-language queries still retrieve
    documents matching any meaningful term. Stopwords are dropped so the keyword
    leg matches on content, not on "how"/"a"/"in"; if a query is all stopwords we
    fall back to every token. BM25 ranking + RRF + rerank handle precision.
    """
    tokens = re.findall(r"\w+", query.lower())
    content = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
    return " OR ".join(f'"{t}"' for t in (content or tokens))


def search_bm25(
    conn: sqlite3.Connection,
    query: str,
    *,
    product: Product | None = None,
    doc_ids: list[str] | None = None,
    limit: int = 10,
) -> list[tuple[Chunk, float]]:
    """BM25 keyword search. Returns (chunk, score) with higher score = better.

    (FTS5's bm25() is lower-is-better, so we negate it for consistency with the
    cosine scores from the vector store.)

    ``doc_ids`` restricts results to those source documents — used to scope an
    EPM query to a single module (e.g. Planning vs FCC) that shares the product.
    """
    match = _fts_query(query)
    if not match:
        return []
    sql = (
        "SELECT c.*, bm25(chunks_fts) AS bm25 "
        "FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
        "WHERE chunks_fts MATCH ?"
    )
    params: list = [match]
    if product is not None:
        sql += " AND c.product = ?"
        params.append(product)
    if doc_ids:
        sql += f" AND c.doc_id IN ({','.join('?' * len(doc_ids))})"
        params.extend(doc_ids)
    sql += " ORDER BY bm25 LIMIT ?"
    params.append(limit)
    return [(_row_to_chunk(row), -row["bm25"]) for row in conn.execute(sql, params)]


def count_by_product(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT product, COUNT(*) AS n FROM chunks GROUP BY product")
    return {row["product"]: row["n"] for row in rows}


def chunks_for_doc(conn: sqlite3.Connection, doc_id: str) -> list[Chunk]:
    """All chunks of a document, in order."""
    rows = conn.execute(
        "SELECT * FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (doc_id,)
    ).fetchall()
    return [_row_to_chunk(row) for row in rows]


def get_document(conn: sqlite3.Connection, doc_id: str) -> Document | None:
    """Reconstruct a full Document from its stored chunks (None if unknown).

    We don't store whole documents — just chunks — so the full text is the
    chunks re-joined in order, and the title is the document's top-level heading.
    """
    chunks = chunks_for_doc(conn, doc_id)
    if not chunks:
        return None
    if chunks[0].section_path:
        title = _clean_heading(chunks[0].section_path[0])
    else:
        title = doc_id.replace("-", " ").replace("_", " ").title()
    return Document(
        id=doc_id,
        product=chunks[0].product,
        title=title,
        source_url=chunks[0].source_url,
        full_text="\n\n".join(c.text for c in chunks),
    )


def top_level_sections(conn: sqlite3.Connection, product: Product) -> list[str]:
    """Distinct top-level section names for a product (drives list_topics)."""
    rows = conn.execute(
        "SELECT DISTINCT section_path FROM chunks WHERE product = ?", (product,)
    )
    tops: set[str] = set()
    for row in rows:
        path = json.loads(row["section_path"])
        if path:
            tops.add(_clean_heading(path[0]))
    return sorted(tops)


# --------------------------------------------------------------------------- #
# CLI smoke test (in-memory)
# --------------------------------------------------------------------------- #


def _smoke_test() -> None:
    conn = connect(":memory:")
    init_db(conn)

    chunks = [
        Chunk.create(product="erp", doc_id="erp-gl", chunk_index=0,
                     text="A reversing journal entry reverses an existing journal.",
                     source_url="https://x/gl", section_path=["General Ledger", "Journals"]),
        Chunk.create(product="erp", doc_id="erp-gl", chunk_index=1,
                     text="Accounts payable manages supplier invoices and payments.",
                     source_url="https://x/gl", section_path=["Payables"]),
        Chunk.create(product="oci", doc_id="oci-compute", chunk_index=0,
                     text="A compute instance runs on a shape defining CPU and memory.",
                     source_url="https://x/oci", section_path=["Compute"]),
    ]
    assert upsert_chunks(conn, chunks) == 3
    assert count_by_product(conn) == {"erp": 2, "oci": 1}
    print("OK: inserted 3 chunks ->", count_by_product(conn))

    # BM25 search finds the right chunk and round-trips into a Chunk model
    hits = search_bm25(conn, "journal entry")
    assert hits and hits[0][0].doc_id == "erp-gl" and hits[0][0].chunk_index == 0
    assert hits[0][0].section_path == ["General Ledger", "Journals"], "section_path lost"
    print(f"OK: 'journal entry' -> {len(hits)} hit(s), top score {hits[0][1]:.3f}")

    # product filter
    erp_hits = search_bm25(conn, "instance shape", product="erp")
    oci_hits = search_bm25(conn, "instance shape", product="oci")
    assert not erp_hits and len(oci_hits) == 1
    print("OK: product filter isolates collections")

    # doc_ids filter: scope within a product to specific source documents
    gl_only = search_bm25(conn, "journal supplier", product="erp", doc_ids=["erp-gl"])
    assert gl_only and all(c.doc_id == "erp-gl" for c, _ in gl_only)
    none_match = search_bm25(conn, "journal supplier", product="erp", doc_ids=["erp-nope"])
    assert none_match == [], "doc_ids filter should exclude non-matching docs"
    print("OK: doc_ids filter scopes within a product")

    # the plan's definition-of-done query shape works
    rows = list(conn.execute(
        "SELECT rowid, text FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 5", ('"journal"',)))
    assert rows, "DoD MATCH query returned nothing"
    print(f"OK: DoD-style 'SELECT rowid,text FROM chunks_fts MATCH' -> {len(rows)} row(s)")

    # idempotency: re-upsert same ids -> count unchanged; text update reflected in FTS
    assert upsert_chunks(conn, chunks) == 3
    assert count_by_product(conn) == {"erp": 2, "oci": 1}, "duplicate rows on re-upsert"
    updated = Chunk.create(product="erp", doc_id="erp-gl", chunk_index=0,
                           text="Depreciation schedules apply to fixed assets.",
                           source_url="https://x/gl", section_path=["Assets"])
    upsert_chunks(conn, [updated])
    assert count_by_product(conn) == {"erp": 2, "oci": 1}
    assert not search_bm25(conn, "journal entry"), "old text still indexed after update"
    assert search_bm25(conn, "depreciation"), "new text not indexed after update"
    print("OK: idempotent upsert; FTS re-synced on text update")

    # get_document reconstructs full text from chunks; top_level_sections lists topics
    doc = get_document(conn, "erp-gl")
    assert doc and doc.product == "erp"
    assert "Depreciation" in doc.full_text and "supplier" in doc.full_text.lower()
    assert get_document(conn, "does-not-exist") is None
    assert "Assets" in top_level_sections(conn, "erp")
    print("OK: get_document + top_level_sections")

    # heading cleanup: strip leading numbering, keep number-only headings intact
    assert _clean_heading("19 Managing Consolidation Journals") == "Managing Consolidation Journals"
    assert _clean_heading("2.3 Journals") == "Journals"
    assert _clean_heading("Compute Shapes") == "Compute Shapes"
    assert _clean_heading("25D") == "25D"
    print("OK: _clean_heading strips numbering, preserves number-only headings")

    conn.close()
    print("\nALL DB SMOKE TESTS PASSED")


if __name__ == "__main__":
    _smoke_test()
