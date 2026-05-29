"""Structure-aware document chunking.

Splits a document into retrievable ``Chunk`` objects on heading boundaries,
preserving the heading hierarchy as ``section_path``. Two front doors:

- ``chunk_html`` — parses HTML, using ``<h1>/<h2>/<h3>`` as section boundaries
  (used for OCI's static HTML pages).
- ``chunk_text`` — parses plain text with Markdown-style ``#``/``##``/``###``
  headings (used for any pre-segmented text).

Both feed ``chunk_blocks``, which does the packing: it groups paragraphs under
the same heading path and packs them toward ``max_tokens`` (default 800),
emitting a new chunk before it would overflow, and hard-splitting any single
paragraph that is itself larger than ``max_tokens``. Step 6 (PDF extraction)
can build its own list of ``Block``s from PDF structure and call
``chunk_blocks`` directly, reusing this packing logic.

Token counts use tiktoken's ``cl100k_base`` as an *approximate* counter —
Voyage does not publish a public tokenizer, and we are targeting a range
(400–800), not an exact budget.

Run the smoke test with::

    python -m shared.chunking
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import tiktoken
from bs4 import BeautifulSoup

from .models import Chunk, Product

MIN_TOKENS = 400
MAX_TOKENS = 800

# Tags whose text we treat as body content (as opposed to section headings).
_CONTENT_TAGS = {"p", "li", "pre", "blockquote", "h4", "h5", "h6"}
_HEADING_TAGS = {"h1", "h2", "h3"}

_ENC = None


def _enc() -> "tiktoken.Encoding":
    global _ENC
    if _ENC is None:
        _ENC = tiktoken.get_encoding("cl100k_base")
    return _ENC


def num_tokens(text: str) -> int:
    """Approximate token count for the embedding model."""
    return len(_enc().encode(text))


@dataclass(frozen=True)
class Block:
    """A paragraph-level unit of content tagged with its heading path."""

    section_path: tuple[str, ...]
    text: str


# --------------------------------------------------------------------------- #
# Parsing: document -> ordered list[Block]
# --------------------------------------------------------------------------- #


def _update_stack(stack: list[tuple[int, str]], level: int, text: str) -> None:
    """Apply a heading of the given level to the running heading stack."""
    while stack and stack[-1][0] >= level:
        stack.pop()
    if text:
        stack.append((level, text))


def _blocks_from_html(html: str) -> list[Block]:
    soup = BeautifulSoup(html, "lxml")
    for noise in soup(["script", "style", "noscript"]):
        noise.decompose()
    root = soup.body or soup

    stack: list[tuple[int, str]] = []
    blocks: list[Block] = []
    for el in root.find_all(sorted(_HEADING_TAGS | _CONTENT_TAGS)):
        text = el.get_text(" ", strip=True)
        if el.name in _HEADING_TAGS:
            _update_stack(stack, int(el.name[1]), text)
            continue
        if not text:
            continue
        # Skip content nested inside other content (e.g. a <p> inside an <li>);
        # the ancestor's get_text already includes it, so this avoids duplicates.
        if any(parent.name in _CONTENT_TAGS for parent in el.parents):
            continue
        blocks.append(Block(tuple(t for _, t in stack), text))
    return blocks


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _blocks_from_pdf(path: str) -> list[Block]:
    """Extract blocks from a PDF using font size for headings.

    Headings are detected as text larger than the body font that appears on at
    least two pages (this drops one-off cover-page display fonts). Running
    headers/footers are dropped by skipping the top/bottom 8% margins. Heading
    levels are ranked by size (largest = level 1), capped at 3 to mirror the
    HTML h1/h2/h3 behaviour. pymupdf is imported lazily so only the ingestion
    image needs the dependency.
    """
    import pymupdf

    doc = pymupdf.open(path)
    try:
        mass: Counter[float] = Counter()
        pages_per_size: dict[float, set[int]] = defaultdict(set)
        for pno in range(doc.page_count):
            for blk in doc[pno].get_text("dict")["blocks"]:
                if blk.get("type") != 0:
                    continue
                for line in blk.get("lines", []):
                    for span in line["spans"]:
                        if span["text"].strip():
                            size = round(span["size"], 1)
                            mass[size] += len(span["text"].strip())
                            pages_per_size[size].add(pno)
        if not mass:
            return []
        body = mass.most_common(1)[0][0]
        heading_sizes = sorted(
            (s for s in mass if s > body + 0.9 and len(pages_per_size[s]) >= 2),
            reverse=True,
        )
        level_map = {s: min(i + 1, 3) for i, s in enumerate(heading_sizes)}

        blocks: list[Block] = []
        stack: list[tuple[int, str]] = []
        for pno in range(doc.page_count):
            page = doc[pno]
            margin = page.rect.height * 0.08
            for blk in page.get_text("dict")["blocks"]:
                if blk.get("type") != 0:
                    continue
                y0, y1 = blk["bbox"][1], blk["bbox"][3]
                if y1 < margin or y0 > page.rect.height - margin:
                    continue  # running header / footer band
                spans = [sp for line in blk.get("lines", []) for sp in line["spans"]]
                text = _norm_ws(" ".join(sp["text"] for sp in spans))
                if not text:
                    continue
                size = round(max((sp["size"] for sp in spans), default=body), 1)
                level = level_map.get(size)
                if level and len(text) >= 3 and not text.isdigit():
                    _update_stack(stack, level, text)
                else:
                    blocks.append(Block(tuple(t for _, t in stack), text))
        return blocks
    finally:
        doc.close()


def _blocks_from_text(text: str) -> list[Block]:
    stack: list[tuple[int, str]] = []
    blocks: list[Block] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            joined = " ".join(line.strip() for line in para).strip()
            if joined:
                blocks.append(Block(tuple(t for _, t in stack), joined))
            para.clear()

    for raw in text.splitlines():
        line = raw.strip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            flush_para()
            _update_stack(stack, len(heading.group(1)), heading.group(2).strip())
        elif not line:
            flush_para()
        else:
            para.append(line)
    flush_para()
    return blocks


# --------------------------------------------------------------------------- #
# Packing: list[Block] -> list[Chunk]
# --------------------------------------------------------------------------- #


def _hard_split(text: str, max_tokens: int) -> list[str]:
    """Last-resort split of a too-long span by raw token windows."""
    enc = _enc()
    toks = enc.encode(text)
    return [enc.decode(toks[i : i + max_tokens]) for i in range(0, len(toks), max_tokens)]


def _split_oversized(text: str, max_tokens: int) -> list[str]:
    """Split a paragraph larger than max_tokens into <=max_tokens pieces."""
    pieces: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not sentence:
            continue
        stok = num_tokens(sentence)
        if stok > max_tokens:
            if cur:
                pieces.append(" ".join(cur))
                cur, cur_tok = [], 0
            pieces.extend(_hard_split(sentence, max_tokens))
            continue
        if cur and cur_tok + stok > max_tokens:
            pieces.append(" ".join(cur))
            cur, cur_tok = [], 0
        cur.append(sentence)
        cur_tok += stok
    if cur:
        pieces.append(" ".join(cur))
    return pieces


def chunk_blocks(
    blocks: list[Block],
    *,
    product: Product,
    doc_id: str,
    source_url: str,
    min_tokens: int = MIN_TOKENS,
    max_tokens: int = MAX_TOKENS,
) -> list[Chunk]:
    """Pack blocks into chunks, never crossing a heading-path boundary.

    ``min_tokens`` is a soft target (packing fills toward ``max_tokens``, so a
    section's trailing chunk may be smaller); ``max_tokens`` is a hard ceiling.
    """
    chunks: list[Chunk] = []
    path: tuple[str, ...] = ()
    buf: list[str] = []
    buf_tok = 0
    idx = 0
    # Paragraphs are joined with "\n\n"; count that separator against the budget
    # so packing many tiny paragraphs doesn't overflow max_tokens.
    join_overhead = len(_enc().encode("\n\n"))

    def emit(text: str) -> None:
        nonlocal idx
        text = text.strip()
        if text:
            chunks.append(
                Chunk.create(
                    product=product,
                    doc_id=doc_id,
                    chunk_index=idx,
                    text=text,
                    source_url=source_url,
                    section_path=list(path),
                )
            )
            idx += 1

    def flush() -> None:
        nonlocal buf, buf_tok
        if buf:
            emit("\n\n".join(buf))
        buf, buf_tok = [], 0

    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if block.section_path != path:
            flush()
            path = block.section_path
        ptok = num_tokens(text)
        if ptok > max_tokens:
            flush()
            for piece in _split_oversized(text, max_tokens):
                emit(piece)
            continue
        added = ptok + (join_overhead if buf else 0)
        if buf and buf_tok + added > max_tokens:
            flush()
            added = ptok  # buffer now empty: no separator
        buf.append(text)
        buf_tok += added
    flush()
    return chunks


def chunk_html(
    html: str, *, product: Product, doc_id: str, source_url: str, **kwargs
) -> list[Chunk]:
    return chunk_blocks(
        _blocks_from_html(html),
        product=product,
        doc_id=doc_id,
        source_url=source_url,
        **kwargs,
    )


def chunk_text(
    text: str, *, product: Product, doc_id: str, source_url: str, **kwargs
) -> list[Chunk]:
    return chunk_blocks(
        _blocks_from_text(text),
        product=product,
        doc_id=doc_id,
        source_url=source_url,
        **kwargs,
    )


def chunk_pdf(
    path: str, *, product: Product, doc_id: str, source_url: str, **kwargs
) -> list[Chunk]:
    return chunk_blocks(
        _blocks_from_pdf(path),
        product=product,
        doc_id=doc_id,
        source_url=source_url,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #


def _smoke_test() -> None:
    # --- HTML, heading hierarchy --------------------------------------------
    html = """
    <html><body>
      <h1>General Ledger</h1>
      <p>The general ledger is the central repository of accounting data.</p>
      <h2>Journal Entries</h2>
      <p>Journals record financial transactions in the ledger.</p>
      <h3>Reversing Journals</h3>
      <p>A reversing journal reverses the amounts of an existing journal.</p>
      <ul><li>Create the original journal.</li><li>Schedule the reversal.</li></ul>
    </body></html>
    """
    hchunks = chunk_html(html, product="erp", doc_id="erp-gl", source_url="https://x/gl")
    paths = [c.section_path for c in hchunks]
    print(f"HTML -> {len(hchunks)} chunks")
    for c in hchunks:
        print(f"  [{c.chunk_index}] {' > '.join(c.section_path)} :: {c.text[:50]!r}")

    assert ["General Ledger"] in paths
    assert ["General Ledger", "Journal Entries"] in paths
    assert ["General Ledger", "Journal Entries", "Reversing Journals"] in paths
    # list items packed under the same h3 path as the preceding paragraph
    rev = next(c for c in hchunks if c.section_path[-1:] == ["Reversing Journals"])
    assert "Schedule the reversal." in rev.text
    assert [c.chunk_index for c in hchunks] == list(range(len(hchunks)))
    assert all(c.id == Chunk.make_id("erp-gl", c.chunk_index) for c in hchunks)
    print("OK: HTML section paths, packing, sequential deterministic ids")

    # --- plain text, Markdown headings --------------------------------------
    text = (
        "# Oracle Cloud Infrastructure\n\n"
        "OCI provides core infrastructure services.\n\n"
        "## Compute\n\n"
        "Compute lets you provision and manage instances.\n\n"
        "### Shapes\n\n"
        "A shape defines the CPU and memory resources of an instance.\n"
    )
    tchunks = chunk_text(text, product="oci", doc_id="oci-compute", source_url="https://x/oci")
    tpaths = [c.section_path for c in tchunks]
    print(f"TEXT -> {len(tchunks)} chunks")
    assert ["Oracle Cloud Infrastructure"] in tpaths
    assert ["Oracle Cloud Infrastructure", "Compute"] in tpaths
    assert ["Oracle Cloud Infrastructure", "Compute", "Shapes"] in tpaths
    print("OK: plain-text Markdown heading paths")

    # --- oversized paragraph splits, all under one path, all <= max ---------
    long_para = "This sentence describes a configuration step in detail. " * 80
    big = "# Big Section\n\n" + long_para
    bchunks = chunk_text(
        big, product="erp", doc_id="erp-big", source_url="https://x/big", max_tokens=50
    )
    print(f"OVERSIZED -> {len(bchunks)} chunks (max_tokens=50)")
    assert len(bchunks) > 1, "oversized paragraph should split into multiple chunks"
    assert all(num_tokens(c.text) <= 50 for c in bchunks), "a chunk exceeded max_tokens"
    assert all(c.section_path == ["Big Section"] for c in bchunks)
    print("OK: oversized paragraph split, every chunk within max_tokens")

    # --- general invariants --------------------------------------------------
    for c in hchunks + tchunks + bchunks:
        assert c.text.strip(), "empty chunk text"
    print("OK: no empty chunks")

    print("\nALL CHUNKING SMOKE TESTS PASSED")


if __name__ == "__main__":
    _smoke_test()
