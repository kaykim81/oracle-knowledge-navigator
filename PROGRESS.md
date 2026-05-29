# Build Progress

Tracks build state phase by phase. See `MASTER_PLAN.md` for the plan, `CLAUDE.md` for the standing rules.

---

## Phase 0: VPS Preparation — ✅ COMPLETE (2026-05-29)

**What was done:**

- Steps 1–4 (env, git init, `.gitignore`): done prior to this session.
- **Step 5** — `.env.example` authored in the dev container with all six placeholders (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `TRAEFIK_NETWORK`, `PUBLIC_HOSTNAME`, `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD_HASH`), including the single-`$` htpasswd caveat in comments.
- **Step 6** — DNS A record `navigator.p36server.com → 89.116.167.171` created at the provider. Verified resolving at public resolvers 8.8.8.8 and 1.1.1.1 (the dev container's own resolver negative-caches, which is a local quirk, not a DNS problem).
- **Step 7** — Both API keys smoke-tested with `curl`: Anthropic `/v1/messages` (claude-sonnet-4-6) → HTTP 200; Voyage `/v1/embeddings` (voyage-3-large) → HTTP 200, **1024-dim** output (confirms the Qdrant vector size for Phase 1).
- **Step 8** — Real `.env` created in the dev container (gitignored, verified not tracked). Non-secret values set; `BASIC_AUTH_USER=demo`, bcrypt hash for password `demo` generated with `htpasswd -nbB` and verified; single `$`, not doubled. API keys filled in by the user and verified working (step 7 above).
- **Steps 9–10** — Scaffolding committed (`.devcontainer/*`, `.gitignore`) as commit `001` and pushed to `origin` (`git@github.com:kaykim81/oracle-knowledge-navigator.git`). `main` in sync with `origin/main`.

**Deviations from the plan (with reasoning):**

- **Develop-here / deploy-to-VPS split.** We author and commit code/config/docs in the dev container (`/workspaces/oracle-knowledge-navigator`) and deploy to the VPS (`/docker/oracle-knowledge-navigator`) separately. Documented in `CLAUDE.md`, `MASTER_PLAN.md` (Phase 0 callout), and `ARCHITECTURE.md`. VPS-side actions (SSH, real `.env`, live URL) are performed at deploy time.
- **Handover docs intentionally gitignored.** `.gitignore` excludes `CLAUDE.md`, `PROJECT_CONTEXT.md`, `ARCHITECTURE.md`, `MASTER_PLAN.md`, so the planning/interview docs stay local and out of the public repo. This deviates from step 9's literal "commit the four handover files," but is deliberate — `PROJECT_CONTEXT.md` contains the job description and interview strategy.
- **Tooling.** `htpasswd` (apache2-utils) was not preinstalled in the dev container; installed it to generate the bcrypt hash. `dig`/`host`/`nslookup` are also absent — DNS checks used Python/raw resolver queries instead.

**Deferred / user-owned, not yet confirmed:**

- **Spend caps.** The plan calls for $20 hard caps on the Anthropic and Voyage dashboards. This is a dashboard action owned by the user — not verifiable from the dev container. **Confirm before any eval runs.**
- **`.env` on the VPS.** The real `.env` currently exists in the dev container. It must be replicated on the VPS at deploy time (the runtime target). Weak `demo/demo` basic-auth is acceptable for a demo but trivially guessable on a public URL; spend caps are the real backstop.
- **`.env.example`** is authored but not yet committed (untracked). Commit it with Phase 1 work. *(Update: committed.)*

---

## Phase 1: Document Ingestion Pipeline — ✅ COMPLETE (2026-05-29)

**Outcome:** Three Oracle product corpora fetched, chunked, embedded, and stored in both Qdrant (vectors) and SQLite/FTS5 (BM25). Full ingest run on the VPS via the ingestion container; counts reconcile across both stores.

| Product | Source | SQLite | Qdrant |
|---|---|---|---|
| erp | 4 Fusion Financials guide PDFs (25D) | 4034 | 4034 |
| epm | 3 EPM admin guide PDFs | 2181 | 2181 |
| oci | 240 crawled HTML pages (60/service × 4) | 1525 | 1525 |
| **total** | | **7740** | **7740** |

~1.7M Voyage tokens embedded (voyage-3-large, 1024-d).

**Steps:**
- 1 — `ingestion/sources/_index.json`: source manifest (11 URLs verified 200).
- 2 — Qdrant in `docker-compose.yml` (v1.15.5, internal-only, bind-mounted).
- 3 — `shared/models.py`: `Chunk`/`Document`/`SearchResult`, deterministic UUID5 ids.
- 4 — `shared/chunking.py`: structure-aware HTML (h1–h3) + Markdown chunking, 400–800 tok.
- 5 — `shared/embeddings.py`: Voyage wrapper, batches of 128, backoff, 1024-d.
- 6 — `ingestion/fetch_docs.py`: PDF download + bounded OCI HTML crawl, resilient, 0.5s rate-limit.
- 7 — `shared/db.py`: SQLite `chunks` + external-content FTS5, idempotent upsert, triggers.
- 8 — `shared/qdrant_store.py`: one collection per product, 1024-d cosine, isolation.
- 9 — `ingestion/ingest.py`: orchestrator (fetch→chunk→embed→both stores) + `chunk_pdf`.
- 10 — `ingestion/Dockerfile` + on-demand `ingestion` compose service; full run on VPS.

**Deviations / decisions (with reasoning):**
- **Mixed-format sourcing.** OCI = HTML crawl (clean static `.htm`), ERP/EPM = whole-guide PDFs (Fusion SaaS HTML books are JS-rendered TOCs a plain HTTP client can't scrape). Decided after probing real pages.
- **New file `shared/qdrant_store.py`** (outside the MASTER_PLAN layout) — approved; avoids duplicating the Qdrant client across ingest + retrieval.
- **PDF chunking** by font-size heading detection + margin header/footer filtering (pymupdf, lazy import so only ingestion needs it). ~99% section-path coverage.
- **OCI crawl** capped at 60 pages/service to balance volume against the PDF corpora.

**Bugs found & fixed during the live run:**
- `.dockerignore` wildcard `ingestion/sources/*/` also excluded `_index.json` → image lacked the manifest. Fixed with explicit subdir excludes.
- `qdrant_store.upsert_chunks` sent thousands of vectors in one request → HTTP write timeout. Fixed: 128-point batches + 120s client timeout.
- `qdrant-client` 1.18.0 vs server 1.15.5 incompatibility warning → pinned client to 1.15.1.
- Chunk packing didn't count `\n\n` separators → some chunks exceeded 800 tokens. Fixed by accounting separator tokens.

**Dependencies added (pinned in `ingestion/requirements.txt`):** pydantic 2.13.4, beautifulsoup4 4.14.3, lxml 6.1.1, tiktoken 0.13.0, voyageai 0.3.7, httpx 0.28.1, qdrant-client 1.15.1, pymupdf 1.27.2.3.

**Deferred / notes:**
- Optional final sanity checks (BM25 query + a vector-search query) recommended but not blocking; counts already reconcile.
- Re-running ingest re-embeds (vectors not cached). An on-disk embedding cache would make retries free — deferred as a future nicety.
- Real `.env` must exist on the VPS (it does) with the Voyage key for the ingestion container.

---
