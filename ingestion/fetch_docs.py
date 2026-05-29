"""Download raw Oracle docs into ingestion/sources/{product}/ per _index.json.

Two fetch modes, driven by the manifest:

- ``pdf`` (ERP, EPM): download each guide PDF to ``{product}/{doc_id}.pdf``.
- ``crawl_html`` (OCI): breadth-first crawl from each service seed, staying
  under the service's ``section_prefix`` (and the global ``allowed_prefix``),
  capped at ``max_pages_per_service``; each page saved as ``{product}/<slug>.html``.

Each product directory also gets a ``_fetched.json`` recording every saved file
with its ``doc_id``, ``title``, ``source_url``, and ``format`` — the input that
``ingest.py`` (step 9) reads. Raw downloads and ``_fetched.json`` are gitignored;
only ``_index.json`` is tracked.

Resilient by design: a failed URL is logged and skipped, never aborting the run.
Rate-limited with a 0.5s delay between requests.

Usage::

    python -m ingestion.fetch_docs                      # everything, manifest caps
    python -m ingestion.fetch_docs --product oci --max-pages 8
    python -m ingestion.fetch_docs --product erp --limit 1
    python -m ingestion.fetch_docs --smoke              # offline helper tests
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("fetch_docs")

SOURCES_DIR = Path(__file__).parent / "sources"
INDEX_PATH = SOURCES_DIR / "_index.json"

REQUEST_DELAY = 0.5
USER_AGENT = "oracle-knowledge-navigator/0.1 (ingestion; +https://navigator.p36server.com)"
TIMEOUT = httpx.Timeout(60.0)


# --------------------------------------------------------------------------- #
# Pure helpers (offline-testable)
# --------------------------------------------------------------------------- #


def slug_for_url(url: str, marker: str = "/Content/") -> str:
    """Stable filename for a crawled HTML page, derived from its URL path."""
    path = urlparse(url).path
    if marker in path:
        path = path.split(marker, 1)[1]
    path = re.sub(r"\.html?$", "", path)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    return f"{slug or 'index'}.html"


def in_crawl_scope(url: str, *, allowed_prefix: str, section_prefix: str) -> bool:
    """True if a URL is an HTML page within the allowed crawl bounds."""
    clean, _ = urldefrag(url)
    if not (clean.startswith(allowed_prefix) and clean.startswith(section_prefix)):
        return False
    path = urlparse(clean).path.lower()
    return path.endswith(".htm") or path.endswith(".html")


def extract_links(html: str, base_url: str) -> list[str]:
    """All absolute, fragment-stripped hrefs found in the page (de-duped, in order)."""
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        absolute, _ = urldefrag(urljoin(base_url, a["href"]))
        if absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out


def page_title(html: str, fallback: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    return fallback


# --------------------------------------------------------------------------- #
# Network fetchers
# --------------------------------------------------------------------------- #


def _get(client: httpx.Client, url: str) -> httpx.Response | None:
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return resp
    except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
        log.warning("FAILED %s (%s)", url, exc)
        return None


def fetch_pdfs(client: httpx.Client, product: str, docs: list[dict], out_dir: Path,
               *, limit: int = 0, force: bool = False) -> list[dict]:
    records: list[dict] = []
    if limit:
        docs = docs[:limit]
    for doc in docs:
        dest = out_dir / f"{doc['doc_id']}.pdf"
        if dest.exists() and dest.stat().st_size > 0 and not force:
            log.info("skip (exists) %s", dest.name)
        else:
            resp = _get(client, doc["url"])
            time.sleep(REQUEST_DELAY)
            if resp is None:
                continue
            dest.write_bytes(resp.content)
            log.info("saved %s (%d KB)", dest.name, len(resp.content) // 1024)
        records.append({
            "product": product, "doc_id": doc["doc_id"], "title": doc["title"],
            "source_url": doc["url"], "format": "pdf", "file": f"{product}/{dest.name}",
        })
    return records


def crawl_service(client: httpx.Client, product: str, service: dict, crawl: dict,
                  out_dir: Path, *, max_pages: int) -> list[dict]:
    allowed_prefix = crawl["allowed_prefix"]
    section_prefix = service["section_prefix"]
    queue: deque[str] = deque([service["seed"]])
    visited: set[str] = set()
    records: list[dict] = []

    while queue and len(records) < max_pages:
        url = queue.popleft()
        clean, _ = urldefrag(url)
        if clean in visited:
            continue
        visited.add(clean)

        resp = _get(client, clean)
        time.sleep(REQUEST_DELAY)
        if resp is None:
            continue

        dest = out_dir / slug_for_url(clean)
        dest.write_text(resp.text, encoding="utf-8")
        records.append({
            "product": product, "doc_id": dest.stem, "title": page_title(resp.text, service["title"]),
            "source_url": clean, "format": "html", "file": f"{product}/{dest.name}",
        })
        log.info("[%s %d/%d] saved %s", service["doc_id"], len(records), max_pages, dest.name)

        for link in extract_links(resp.text, clean):
            if link not in visited and in_crawl_scope(
                link, allowed_prefix=allowed_prefix, section_prefix=section_prefix
            ):
                queue.append(link)

    return records


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def run(products: list[str], *, max_pages_override: int | None, limit: int, force: bool) -> None:
    index = json.loads(INDEX_PATH.read_text())
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
                      follow_redirects=True) as client:
        for product in products:
            cfg = index["products"][product]
            out_dir = SOURCES_DIR / product
            out_dir.mkdir(parents=True, exist_ok=True)
            mode = cfg["fetch_mode"]
            log.info("=== %s (%s) ===", product, mode)

            if mode == "pdf":
                records = fetch_pdfs(client, product, cfg["documents"], out_dir,
                                     limit=limit, force=force)
            elif mode == "crawl_html":
                cap = max_pages_override or cfg["crawl"]["max_pages_per_service"]
                records = []
                for service in cfg["services"]:
                    records += crawl_service(client, product, service, cfg["crawl"],
                                             out_dir, max_pages=cap)
            else:
                log.error("unknown fetch_mode %r for %s", mode, product)
                continue

            (out_dir / "_fetched.json").write_text(json.dumps(records, indent=2))
            log.info("%s: %d files -> %s/_fetched.json", product, len(records), product)


def _smoke_test() -> None:
    base = "https://docs.oracle.com/en-us/iaas/Content/Compute/Concepts/computeoverview.htm"
    assert slug_for_url(base) == "Compute_Concepts_computeoverview.html", slug_for_url(base)

    allowed = "https://docs.oracle.com/en-us/iaas/Content/"
    section = "https://docs.oracle.com/en-us/iaas/Content/Compute/"
    assert in_crawl_scope(base, allowed_prefix=allowed, section_prefix=section)
    # out of section
    assert not in_crawl_scope(
        "https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/overview.htm",
        allowed_prefix=allowed, section_prefix=section)
    # not html
    assert not in_crawl_scope(
        "https://docs.oracle.com/en-us/iaas/Content/Compute/x.pdf",
        allowed_prefix=allowed, section_prefix=section)
    # off-host / off-prefix
    assert not in_crawl_scope("https://example.com/Content/Compute/x.htm",
                              allowed_prefix=allowed, section_prefix=section)
    print("OK: slug + crawl-scope filtering")

    html = """
    <html><head><title>Overview of Compute</title></head><body>
    <a href="Tasks/instances.htm">Instances</a>
    <a href="Tasks/instances.htm#section">dup w/ fragment</a>
    <a href="https://example.com/x">external</a>
    <a href="../Network/overview.htm">other service</a>
    </body></html>
    """
    links = extract_links(html, base)
    assert "https://docs.oracle.com/en-us/iaas/Content/Compute/Concepts/Tasks/instances.htm" in links
    # fragment de-duped to same URL
    assert links.count(
        "https://docs.oracle.com/en-us/iaas/Content/Compute/Concepts/Tasks/instances.htm") == 1
    in_scope = [l for l in links if in_crawl_scope(l, allowed_prefix=allowed, section_prefix=section)]
    assert all("/Compute/" in l for l in in_scope)
    assert page_title(html, "fallback") == "Overview of Compute"
    assert page_title("<html><body><h1>Heading</h1></body></html>", "fb") == "Heading"
    assert page_title("<html><body>no title</body></html>", "fb") == "fb"
    print(f"OK: link extraction ({len(links)} found, {len(in_scope)} in-scope), title parsing")

    print("\nALL FETCH_DOCS SMOKE TESTS PASSED")


def main() -> None:
    ap = argparse.ArgumentParser(description="Download Oracle docs per _index.json")
    ap.add_argument("--product", default="all", help="erp | epm | oci | all")
    ap.add_argument("--max-pages", type=int, default=None, help="override per-service crawl cap")
    ap.add_argument("--limit", type=int, default=0, help="cap PDFs per product (0 = all)")
    ap.add_argument("--force", action="store_true", help="re-download existing PDFs")
    ap.add_argument("--smoke", action="store_true", help="run offline helper tests and exit")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.smoke:
        _smoke_test()
        return

    index = json.loads(INDEX_PATH.read_text())
    products = list(index["products"]) if args.product == "all" else [args.product]
    run(products, max_pages_override=args.max_pages, limit=args.limit, force=args.force)


if __name__ == "__main__":
    main()
