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

> **Superseded 2026-05-31.** These counts reflect the original chunker, which over-fragmented the corpus (mean ~213 tok/chunk). After the chunking-quality fix the corpus was re-ingested to **erp 947 / epm 873 / oci 754 = 2574** (both stores reconcile). Same source text, same ~1.7M embedded tokens — just packed into right-sized chunks. See the "Chunking quality fix" entry at the end of this file.

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

## Phase 2: Hybrid Retrieval Library — ✅ COMPLETE (2026-05-29)

**Outcome:** `shared/retrieval.py` exposes one engine — `async retrieve(query, product, mode, top_k)` — with three modes, used by all MCP servers and measured by the eval scorecard. 

**Steps:**
- 1 — `retrieve()` scaffold + signature; `product` is a plain string (no hardcoded list).
- 2 — `vector_only`: embed query → Qdrant search → top_k; `latency_ms` stamped (added `latency_ms`/`rerank_latency_ms` to `SearchResult`).
- 3 — `hybrid`: vector + BM25 in parallel (`asyncio.gather`/`to_thread`), fused with RRF (k=60).
- 4 — `hybrid_rerank`: 30 hybrid candidates → Voyage `rerank-2` → top_k; surfaces rerank latency separately.
- 5 — CLI debugging tool: `python -m shared.retrieval --query .. --product .. --mode .. --top-k ..`.
- 6 — verified on the VPS via the ingestion container across 5 queries × 3 modes.

**Definition of done — met:**
- Modes return sensibly different results across 5 queries.
- **p95 hybrid_rerank latency 323 ms** (target < 2000 ms).
- No hardcoded product list.

**Verification highlights:**
- "how do I reverse a journal entry" (erp): pure-vector returned clean reversal hits; `hybrid` (RRF) mixed in a less-precise clearing-account chunk; `hybrid_rerank` restored precision (Journal Reversals 0.83). Crisp `vector → hybrid → hybrid+rerank` story for the scorecard.
- Federation-ready: EPM (consolidation) and OCI (object storage, BYOIP) queries all retrieve correctly.

**Decisions / fixes (with reasoning):**
- `SearchResult` extended with optional `latency_ms` + `rerank_latency_ms` (the planned `-> list[SearchResult]` return type had no timing fields).
- `db._fts_query` ORs terms instead of AND — strict AND made the BM25 leg return nothing for natural-language queries (hybrid collapsed to vector-only). OR restores recall; RRF + rerank handle precision.
- `db.connect(check_same_thread=False)` — BM25 runs in an `asyncio.to_thread` worker.
- RRF `k=60`; rerank candidate pool = 30; reranker = Voyage `rerank-2`.

**Deferred / notes:**
- The Phase 7 eval set should include a **stronger BM25-favoring query** (exact identifier / shape name / error code). The BYOIP query found the right page in all three modes, so it doesn't isolate BM25's unique value.
- **Operational:** the ingestion image must be rebuilt (`docker compose run --rm --build ingestion`) whenever `shared/` changes, or container-side runs use a stale copy.

---

## Phase 3: First MCP Server — ERP — ✅ COMPLETE (2026-05-29)

**Outcome:** A working ERP MCP server (`mcp_servers/erp/server.py`) over MCP streamable-HTTP, backed by the shared hybrid retrieval engine scoped to `erp`. Verified end-to-end from an MCP client container on the VPS.

**Steps:**
- 1 — SDK: official `mcp` 1.27.2, FastMCP server, **streamable-http** transport (clean; no stdio fallback needed).
- 2–4 — Three tools: `search_docs` (→ `retrieve(product='erp')`), `get_document` (reconstructs a Document from its chunks), `list_topics`; tool docstrings spell out IN/OUT of scope for federation routing.
- 5 — `mcp_servers/erp/Dockerfile` (python:3.11-slim, `/health` via stdlib-urllib healthcheck).
- 6 — `erp-mcp` compose service: internal network, `restart: unless-stopped`, SQLite mounted `:ro`.
- 7 — live MCP client test on the VPS.

**Definition of done — met:**
- `list_tools` → 3 tools with correct scoped descriptions.
- `search_docs("reversing journal entries")` → relevant journal-reversal chunks (rerank 0.82/0.80/0.79).
- `get_document(doc_id)` → full document text (753 KB reconstructed).
- Logs clean; container `Up (healthy)`.

**Decisions / additions:**
- `shared/db.py`: `get_document`, `top_level_sections`, `chunks_for_doc`; `connect(read_only=True)` for the read-only bind mount (verified it reads and rejects writes).
- `server.py` opens SQLite read-only and adds a `/health` route (FastMCP `custom_route`).

**Deferred polish (cosmetic, not blocking):**
- `get_document` title falls back to `doc_id` when the first chunk is front-matter with no heading (e.g. shows `erp-general-ledger` instead of "Using General Ledger"). Improve title derivation (e.g. most common top-level section, or carry the manifest title).
- `list_topics` entries include PDF chapter-number prefixes ("2 Journals"); strip leading numbers.

**Operational:** any MCP server image must be rebuilt (`docker compose up -d --build <svc>`) when `shared/` changes.

---

## Phase 4: Clone for OCI and EPM — ✅ COMPLETE (2026-05-29)

**Outcome:** Three federated MCP servers running, each scoped to one product, each returning results only from its own collection.

**Approach (chose the step-5 refactor over literal copies):**
- `shared/mcp_server.py`: `build_server(product, name, port, instructions, *descriptions)` factory holding the common tool bodies, read-only DB wiring, and `/health` route.
- Per-product `server.py` files are thin (~55 lines of product/port/scope text): `erp` (8001), `oci` (8002), `epm` (8003). ERP refactored to use the factory.
- Each `oci`/`epm` gets its own Dockerfile + requirements (identical deps); compose gains `oci-mcp` + `epm-mcp` services (internal, SQLite `:ro`).
- EPM descriptions explicitly note the ERP↔EPM boundary (ERP records transactions; EPM consolidates/plans on top).

**Definition of done — met:** live MCP client test on the VPS — each server's `search_docs` returned only its product's chunks (asserted `products == {product}`); all three `Up (healthy)`.

---

## Phase 5: Orchestrator Agent — ✅ COMPLETE (2026-05-29)

**Outcome:** `orchestrator/` runs a Claude Sonnet 4.6 agent loop (Anthropic SDK directly, no LangChain/LangGraph) that connects to all three MCP servers, namespaces their tools per product, routes queries, and returns `{answer, trace, latency_ms}`. Verified on the VPS.

**Steps:**
- 1–3 — `orchestrator/agent.py` + `prompts/system.md`: `OrchestratorAgent` connects to the 3 servers, namespaces tools (`erp_`/`oci_`/`epm_` × search_docs/get_document/list_topics = 9 tools), manual tool-use loop, structured trace. Server scope flows from each MCP server's `instructions` into the system prompt (DRY). Prompt caching on the system+tools prefix.
- 4 — `orchestrator/server.py`: FastAPI `POST /query` ({question, retrieval_mode?} → {answer, trace, latency_ms}) + `/health`; lifespan opens/closes MCP sessions.
- 5 — Dockerfile (uvicorn :8000) + compose `orchestrator` service (internal, ANTHROPIC_API_KEY via env_file, depends_on the 3 MCP servers `service_healthy`).
- 6 — live test on the VPS.

**Definition of done — met (5 representative questions, all routes correct):**
- single ERP/OCI/EPM → routed to only that product (1 tool call each).
- cross ERP↔EPM data-flow question → routed to **both** erp + epm (4 tool calls), answer synthesized from both.
- out-of-scope (weather) → **0 tool calls**, graceful refusal.

**Decisions:**
- Manual tool-use loop (not the SDK tool-runner) — needed for the structured trace.
- Adaptive thinking off by default (predictable latency + clean traces); one-line tunable.
- Persistent MCP sessions via AsyncExitStack, managed by the FastAPI lifespan.
- `retrieval_mode` override on `/query` so the Phase 7 eval runner can compare modes.

**Deferred / notes:**
- **Latency:** ~19–22s single-product, ~43s cross-product — driven by Claude generating long verbose answers across two sequential calls (retrieval itself is ~300ms). Address in Phase 8: stream answers in the UI and/or tighten answer length in the system prompt. Prompt caching is already in place.
- Routing validated on 5 cases covering all paths; the plan suggests ≥10 — expand during polish if time allows.

---

## Phase 6: Streamlit Demo UI — ✅ COMPLETE (2026-05-29)

**Outcome:** Live, public, working demo at **https://navigator.p36server.com**, gated by Traefik basic auth — the interview's "live URL" success criterion.

**Steps:**
- 1–2 — `ui/app.py`: question box, 4 sample buttons (ERP/OCI/EPM/cross), cited answer, expandable agent trace (server, top chunks with score/section/source/snippet, per-step latency), total-latency banner, "why federation" sidebar + repo/eval links.
- (support) `orchestrator/agent.py`: trace enriched with structured top-chunk info so the UI can render chunks (Claude still receives full results).
- 3 — `ui/Dockerfile`: Streamlit on :8501, `/_stcore/health` healthcheck.
- 4 — compose `ui` service on `navigator-internal` + external `traefik_proxy`; Traefik labels (Host `${PUBLIC_HOSTNAME}`, websecure, `mytlschallenge` TLS, basic-auth from `${BASIC_AUTH_USER}:${BASIC_AUTH_PASSWORD_HASH}`, port 8501).
- 5 — browsed the live URL.

**Definition of done — met:** live URL serves the demo; basic-auth prompt gates it (demo/demo); cross-product sample renders an answer with a two-server (erp+epm) trace.

**Notes:**
- Streamlit websockets passed through Traefik on defaults — no `enableXsrfProtection=false` needed.
- The single-`$` bcrypt hash in `.env` interpolated correctly into the Traefik basic-auth label (compose doesn't re-interpolate substituted values).
- Latency (Phase 5 note) still applies — a spinner covers the wait; streaming is a Phase 8 candidate.

---

## Phase 7: Evaluation Harness — ✅ COMPLETE (2026-05-29)

**Outcome:** A rigorous two-level eval. Full details + tables in `TEST_LOG.md`.

**Built:**
- `evals/dataset.jsonl` — 45 hand-built questions (30 single, 10 cross, 5 adversarial).
- `evals/runner.py` — end-to-end: query orchestrator ×3 modes/question, LLM-as-judge (Sonnet 4.6, Batches API), routing/recall/latency, `{ts}.jsonl` + `{ts}_summary.md`.
- `evals/retrieval_eval.py` — retrieval-level scorecard: recall@k + MRR on `retrieve()` directly, two relevance bars (lenient text / strict section), per-category breakdown. Near-free + fast.
- `evals/Dockerfile` + compose `evals` service (profiles: tools).

**Definition of done — met (with nuance):** `docker compose run evals` produces JSONL + markdown summary. Both criteria hold **per regime**: hybrid > vector on recall for **cross-product**; rerank > hybrid on **adversarial** retrieval (recall@1 40%→80%) and on judged answer quality (3.83 vs 3.22).

**Headline finding:** mode value is **input-dependent**. Aggregate (dominated by 30 easy single questions) shows vector strongest, masking that **rerank doubles top-1 precision on adversarial questions** and hybrid wins on cross-product. Honest per-category decomposition > a forced monotonic curve.

**Debug done (plan order):** (1) metric too lenient → added strict section bar + per-category; (2) **real bug fixed** — BM25 OR-ed stopwords, polluting RRF and dragging hybrid below vector; now ORs content words only; (3) candidate pool already 30.

**Deferred / notes:**
- Full 45-question **end-to-end** judged run not run at scale (only a 6-question dry run, which confirmed rerank>hybrid on quality); optional — the retrieval scorecard + per-category is the headline. Costs ~1hr + spend.
- Operational gap found: rebuilding MCP servers requires restarting the orchestrator (stale persistent sessions). Add reconnection-on-failure in Phase 8.

---

## Phase 8: Polish & Documentation — ✅ COMPLETE (2026-05-30)

**Outcome:** Repo is public and presentable; the demo is faster and more robust. All four user-set priorities done and verified live; remaining items are manual/operational.

**What shipped:**
- **`README.md`** — problem statement, ASCII architecture diagram, quick start, inline per-category eval table + the honest "input-dependent" finding, "what I'd do next", tech-stack rationale. Repo pushed to GitHub (success criterion #5 met).
- **Routing hardening** — adversarial routing **20% → 100%**, via context engineering only: a "route by the distinctive concept, not generic vocabulary" principle in `system.md` + sharper ERP/EPM/OCI scope descriptions encoding real Oracle product boundaries. Verified by re-running `EVAL_CATEGORY=adversarial`; the retrieval scorecard is provably unchanged (the edits only affect what the router reads, not `retrieve()`). Encoded durable boundary facts, not question→answer mappings — generalizes, doesn't overfit the 5 eval questions.
- **Streaming** — new `POST /query/stream` (SSE) + `query_stream()` generator emitting `tool_call` / `answer_delta` / `done` events; UI builds the trace live and streams the answer token-by-token. `POST /query` left unchanged so the eval's JSON contract holds. Verified on the VPS via CLI `--stream` **and** in-browser through Traefik (no proxy buffering).
- **Per-request MCP sessions** — fixed the stale-session bug (an aborted request poisoned a shared session → HTTP 500 until manual restart). `connect()` now only discovers tools; `query`/`query_stream` open + close sessions per request via `AsyncExitStack`. Verified by reproducing the original Ctrl-C trigger: no 500s, no restart.
- **Nits** — `_clean_heading()` strips leading section numbering from `list_topics` + `get_document` titles (PDF corpora carried "19 Managing…"); humanized the `get_document` doc_id fallback (`erp-gl` → "Erp Gl"). Both Phase 3 deferred items now closed.
- **`EVAL_CATEGORY` filter** added to `runner.py` + `retrieval_eval.py` for cheap per-category re-runs.
- **`DEMO_SCRIPT.md`** — 5–7 min interview walkthrough (kept local; gitignored like the other interview-context docs).

**Definition of done — met:** a stranger can clone the public repo, read the README, and run it with their own keys.

**Deviations from the plan (with reasoning):**
- **Streaming and routing hardening are Phase 9 stretch items in the plan**, pulled into Phase 8 at the user's direction — the eval gave routing a concrete, measurable target (the 20%), and streaming addresses the demo's one real weakness (latency). Highest-value polish, so prioritized.
- `DEMO_SCRIPT.md` is gitignored rather than committed (it's interview prep, like `PROJECT_CONTEXT.md`).

**Deferred (manual/operational, user-owned):**
- 90-second backup screen capture → unlisted YouTube, link from README.
- `v1.0` git tag.
- VPS state backup (`docker compose down` → tar `./data/` → copy off-VPS → re-up).
- Rebuild the MCP server images on the VPS to pick up the `_clean_heading` change (cosmetic; doesn't affect routing/retrieval).

**Notes:** full methodology, before/after numbers, and the verified-fix records are in `TEST_LOG.md`.

---

## Phase 9: Stretch Goals — ✅ PARTIAL (2026-05-30)

The plan lists five optional stretch goals and says to pick one or two. Three of the five ended up done — streaming and routing robustness were pulled into Phase 8 (recorded above); the **cost dashboard** was built here. The other two (tenant_id primitive, live-data MCP stub, query decomposition) are deliberately **not** attempted, to keep the demo crisp per the plan's guidance.

**Cost dashboard (stretch goal #5):**
- `orchestrator/agent.py`: `claude-sonnet-4-6` pricing constants ($3/$15 base, $3.75 cache-write 5m, $0.30 cache-read — Anthropic list price). `query()` and `query_stream()` accumulate token usage across every step of the tool-use loop and return/emit a `cost` object (`{input/output/cache tokens, model, usd}`). `POST /query` gains the key (the eval ignores unknown keys, so its contract holds); the streaming `done` event carries it; the CLI prints it in both modes.
- `ui/app.py`: a **cost panel below the trace** — `st.metric`s for USD, output tokens, and "input served from cache" %, plus a per-token caption.

**Honest scoping (recorded so it's defensible in the interview):** this tracks **only the orchestrator's Claude spend**. The Voyage embed/rerank cost lives inside the MCP servers' `retrieve()` and is **not** counted. Labelled "LLM cost" / "Claude only" in code comments, CLI output, and the UI caption — never implying it's the full bill. The cache-savings % is `cache_read / total_input`, labelled "input served from cache", which turns the existing prompt-caching work into a visible number (~0% on a cold first query, ~90%+ once the system+tools prefix is cached).

**Per-question cost (sanity figures, list price):** single-product ~$0.01–0.017, cross-product ~$0.03. Verified arithmetically; live VPS verification pending alongside the rest of the Phase 8/9 runtime checks.

**Not attempted (deliberately):** `tenant_id` schema primitive, live-Oracle-data MCP stub, query decomposition, and any further stretch work — the demo is feature-complete and the plan warns against piling on.

---

## Post-build UI polish (2026-05-30)

Small demo-quality tweaks after the feature work, on top of the streaming + cost-panel UI:
- **Randomized sample questions** — each of the four sample buttons now draws from a pool (4–5 questions each) at random, avoiding an immediate repeat via a per-label `_last_sample` marker in session state. Makes the demo feel live across repeated clicks. Cross-product pool stays federation-exercising (every entry spans ERP↔EPM). Noted in `DEMO_SCRIPT.md`: buttons rotate, so click for a specific question or narrate whatever appears.
- **Eval scorecard link** — sidebar link now points directly at `…/tree/main/evals/results` (derived from `REPO_URL`) instead of the repo root.
- **README** — removed the stale "streaming" entry from "what I'd do next" (it's built); architecture diagram shows `POST /query/stream` (SSE); quick-start notes `--stream` and the per-question cost print.

These are in the UI image — rebuild on the VPS (`docker compose up -d --build ui`) to see the sample/link changes live.

---

## Chunking quality fix (2026-05-31)

**What prompted it.** Reconciling the Phase 1 counts: 7740 chunks vs. ~1.7M embedded Voyage tokens is only **~213 tokens/chunk** — far below the stated 400–800 target. Measuring the live `chunks.db` (same tiktoken `cl100k_base` counter, so the gap is real, not tokenizer drift): mean 213, **median 137**, 84% of chunks under 400, one chunk of 1 token. ERP (PDF) was worst at mean **161**.

**Two root causes (both in `shared/chunking.py`):**
1. **No real floor + no cross-heading packing.** `chunk_blocks` flushed on every `section_path` change, so chunk granularity *was* section granularity — 93% of (doc, section) groups produced exactly one chunk, however tiny. `min_tokens` was accepted as a parameter and described in the docstring but **never referenced in the packing body** — dead code.
2. **PDF heading over-detection.** `_blocks_from_pdf` treated *every* font size `> body+0.9` (on ≥2 pages) as a heading, mapping all sizes beyond the 3rd onto level 3. Oracle Fusion guides use many larger/bold sizes (table headers, run-in labels, captions) → **~866 distinct heading paths/doc** for ERP, each forcing a flush into a sliver.

**Fix.**
- **(a) Min-token floor.** Packing now fills across heading boundaries, ending a chunk at a boundary only once it has reached `min_tokens` (hard ceiling `max_tokens` unchanged). Merged chunks are labelled with the **longest shared heading path** (new `_common_prefix` helper). `min_tokens` is now functional.
- **(b) Tighter PDF headings.** Only the **top-3 sizes above body** are headings (was: all larger sizes → level 3); a heading must also be **≤12 words** (`_MAX_HEADING_WORDS`), filtering emphasized body / table cells.

**Verified locally (offline — chunking costs nothing; only re-embedding costs Voyage).** Smoke test (`python -m shared.chunking`) extended with a floor/merge case, all pass. Measured against the real PDFs present in the repo (1 ERP + 3 EPM):

| Metric (ERP general-ledger PDF) | Before | After |
|---|---|---|
| Mean tokens/chunk | 161 | 729 |
| % chunks under 400 | ~84% | 0% |
| Distinct heading paths/doc | ~866 | 70 |

Post-fix `section_path`s read like a real TOC (`2 Journals > Reverse Journals`, `3 Allocations and Periodic Entries > Recurring Journals`). Note: tiktoken is **not additive across joins**, so packing (which sums per-block counts) lands chunks a few % under the budget — the smoke test allows a 50-token tolerance on the floor.

**Caveat.** The synthetic smoke test can't exercise the pymupdf path; (b) is verified empirically via a measurement script over the 4 committed PDFs, not a unit test. Only `erp-general-ledger.pdf` of the 4 ERP source PDFs is committed locally; the other 3 were fetched at ingest time on the VPS.

**Re-ingest — done on the VPS (2026-05-31).** Voyage spend cap confirmed first. Cleared the old SQLite chunks, rebuilt the ingestion image, and re-ran fetch → chunk → embed → write with `--recreate` (fresh Qdrant collections). New reconciled counts (both stores `OK`):

| Product | Old | New |
|---|---|---|
| erp | 4034 | 947 |
| epm | 2181 | 873 |
| oci | 1525 | 754 |
| **total** | **7740** | **2574** |

~3× fewer chunks; implied mean ~640 tok/chunk (was 213), squarely in the 400–800 band. Same source text and ~1.7M embedded tokens, just packed correctly — so embedding cost was unchanged, not cheaper. All 4 ERP PDFs (incl. the three not committed locally) chunked without anomaly. The Phase 1 count table above is annotated as superseded by these numbers.

---

## Post-chunking-fix eval impact & grounding investigation (2026-05-31)

After the re-ingest we re-ran the evals to check the chunking fix didn't regress retrieval. It did move the numbers, in three distinct ways — and chasing the third uncovered a real (pre-existing) grounding weakness. Full eval detail is in `TEST_LOG.md`; this is the build-state summary. Commits: chunking `e0714e2`, floor `6e6b9f3`, grounding prompts `439aeb2` + `45289a3`, scorecards `a7b6fc0`.

**1. Retrieval recall — TEXT bar held, SECTION bar regressed.** `retrieval_eval` TEXT-bar recall stayed ~98% (content still retrievable). The strict SECTION bar (keyword in section path) dropped (`hybrid_rerank` MRR 0.78→0.61) — an inherent cost of coarser chunks: merged chunks carry the *longest shared* heading path, so breadcrumbs are shallower. **Confirmed not fixable by tuning:** an offline experiment over four labeling rules (common-prefix / first-block / deepest / dominant) moved recoverability only 78%→80%, and reverting to the pre-fix heading detection only 80%→84%. The old over-fragmentation was accidentally optimal for a path-keyword metric. Accepted as a known tradeoff — content retrieval (the TEXT bar) is what feeds answer quality, and it held.

**2. Judge quality "drop" — largely an eval artifact.** A judge re-run showed correctness/groundedness down vs the 2026-05-29 baseline, but that baseline pre-dates Phase 8/9 code changes (not a clean A/B) and, decisively, the judge is shown only a **200-char snippet** of each chunk ([agent.py](orchestrator/agent.py) `_summarize_result`) — which covered ~35% of the old tiny chunks but only ~8% of the new ~660-tok ones, so groundedness was under-measured. The *answering* model received full chunks (the snippet is trace-only), so answers weren't degraded by size. **Fixed** (judge now scores full chunk text) — see "Coverage gap + eval-harness fixes" below.

**3. Hallucination on weak retrieval — real, pre-existing, only partly fixed.** The lowest-groundedness rows were genuine retrieval misses: off-topic/boilerplate chunks returned, then answered from general knowledge. Root cause (verified): **no relevance floor in `retrieve()`**, so the orchestrator's "abstain if nothing relevant" rule never fired (retrieval always returned top_k). Attempted fixes:

- **Relevance floor (`6e6b9f3`, fix #1) — SHELVED.** Env-tunable `RETRIEVAL_MIN_RERANK_SCORE`, applied to `hybrid_rerank` only (the sole calibrated-score mode). Verified on the VPS: at 0.6 it cleanly makes single-product coverage-gap queries abstain (off-topic ~0.50–0.55) while keeping good hits (~0.71–0.84). **But** it craters cross-product recall (90%→25%): legitimate cross-product chunks also score ~0.5 (each addresses only part of a compound query), the *same band* as off-topic boilerplate — no global threshold separates them. Default left **inactive (0.1)**; the mechanism stays for single-product deployments.
- **Grounding prompt (`439aeb2`, fix #3) — insufficient.** Strengthened "abstain when passages don't address the question." The model instead searched harder and produced a confident answer *with* citations — better traceability, but it pulled Financial-Consolidation (FCC) scenario content into a *Planning* answer.
- **Sub-domain-aware prompt (`45289a3`) — partial improvement.** Told the model that EPM spans distinct modules (Planning/FCC/Narrative) that don't cross-apply, to use each chunk's source doc, and to honor in-text applicability notes. Re-run: the answer now **leads with Planning-genuine content, labels its FCC-sourced quotes, and points to the Planning guide as the authority** — but still blends some FCC material.

**The deeper root cause:** the `epm` knowledge base conflates three EPM modules in one Qdrant collection, so a Planning question retrieves FCC chunks. Real fix is data/architecture (module scoping) — **now implemented, see "Cross-module bleed fix" below.**

**Honesty note — limits of automated verification.** A fact-check agent flagged the FCC-sourced answer as "well-cited-but-wrong" because the FCC guide's Table 14-11 marks those scenario properties "not used in Financial Consolidation and Close." On review that verdict **over-read the disclaimer**: those properties (scenario Start/End Yr, Exchange Rate Table) are almost certainly genuine *Planning* properties — the FCC note means "FCC ignores these," implying they apply elsewhere. At this depth, both the system's answers and our automated checks carry Oracle domain uncertainty; reliable adjudication needs an SME, not another agent pass.

---

## Cross-module bleed fix — module-scoped EPM retrieval (2026-05-31)

The "deeper root cause" above, fixed. The `epm` collection holds three module guides (Planning/FCC/Narrative); a Planning question retrieved FCC chunks and the model synthesized them. Chosen fix: **routing-based module scoping** — the most reliable option, since it makes the bleed *structurally impossible* given correct routing, and reuses the tool-selection mechanism already measured at 100% on cross-product routing. Crucially, **no re-ingest** was needed — `doc_id` is already in the Qdrant payload and SQLite.

Commits: `e26bf0f` (data-layer filter), `e36256a` (per-module tools), `b3af7a7` (prompt).

1. **`doc_ids` filter in the data layer.** `qdrant_store.search` (Qdrant `MatchAny` on `doc_id`) and `db.search_bm25` (`WHERE doc_id IN (…)`), threaded through `retrieve()` and all three modes. Smoke-tested in both stores.
2. **Per-module EPM search tools.** `build_server` gained an optional `module_searches`; the EPM server now exposes **`search_planning` / `search_fcc` / `search_narrative`** (each scoped to its `doc_id`) instead of a single `search_docs` — routing to a module's tool *cannot* return another module's chunks. ERP/OCI unchanged (single `search_docs`). The orchestrator auto-discovers tools (no change); the eval bypasses MCP (calls `retrieve()` directly), so it's unaffected.
3. **Prompt routing guidance.** Pick the EPM module tool the question names; don't call a sibling module's tool to fill a gap (abstain instead).

**Verified live on the VPS, both directions:**
- *"manage scenarios and versions in EPM Planning"* → all 5 searches hit **`epm_search_planning`**, every citation is the Planning guide, **zero FCC content**. The answer is honestly bounded — it presents the Planning material it found and notes the detailed dimension-editor CRUD lives in the companion *Administering Planning* guide that wasn't ingested, rather than fabricating it from FCC. (Same question previously pulled FCC scenario-CRUD — the original bug.)
- *"post a consolidation journal during the close"* → all 4 searches hit **`epm_search_fcc`**, grounded in the FCC guide (Ch. 19 Managing Consolidation Journals). Routing discriminates the two vocabulary-sharing modules in both directions.

**Why this worked where prompts didn't:** three attempts at the Planning question — (1) no fix → confident FCC answer; (2) prompt-only (grounding + sub-domain awareness) → still blended FCC ("well-cited-but-wrong"); (3) module-scoped tools → routes to `search_planning`, FCC structurally impossible. Tool-routing beat asking the model to police itself — the reliability argument held.

**Residual / future work:** both follow-ups are now **done** — the coverage gap was closed by ingesting *Administering Planning*, and the judge-snippet eval bug was fixed. See "Coverage gap + eval-harness fixes" below.

---

## Coverage gap + eval-harness fixes, and validation (2026-05-31)

The two residuals from the cross-module work, both closed. Commits: `d0ff48a` (source), `60c4b49` + `5321228` (judge), `bc2d330` (EVAL_IDS).

**Coverage gap — closed.** Added *Administering Planning* (book `pfusa`, E94139 — the core custom-app admin guide with the Dimension-Editor Scenario/Version member CRUD) as `epm-planning-admin`, folded into the `search_planning` module (`doc_ids=[epm-planning, epm-planning-admin]`). URL verified 200/application/pdf; quick local chunk-check (dev container, free) confirmed 551 healthy chunks containing the missing content before spending. Re-ingested **EPM only, no `--recreate`** (additive upsert; ERP/OCI untouched): epm **873 → 1424**, reconciled (`sqlite=qdrant=1424`), ~1.0M Voyage tokens. Live verified: *"create and edit Scenario and Version members"* now routes to `epm_search_planning` and returns the actual Dimension-Editor steps cited to the new guide.

**Judge-snippet eval bug — fixed.** The LLM judge scored groundedness against the 200-char trace snippet (~8% of a ~660-tok chunk), under-measuring it. Now: trace chunks carry full `text` alongside the short `snippet` (UI unchanged; Claude's context/cost unchanged — trace is built separately); `runner._chunks_from_trace` captures `text` **and** matches all `search*` tools (it was silently dropping EPM's `search_planning/fcc/narrative` chunks — a regression from the module-tools change); judge + recall use full text. Also hardened `judge._parse` to coerce scores to int (a quoted `"4"` had crashed the summary aggregation). Added `EVAL_IDS` to run specific questions cheaply.

**Validation (cheap, end-to-end — full 45×3 deferred).** Per cheap-check-first: a 6-question slice showed groundedness recover **~2.4 → ~4.5** (the snippet artifact gone); then an `EVAL_IDS` end-to-end run on the two changed paths gave **`epm-plan-scenario` correctness 5.0 / groundedness 5.0** (the original "well-cited-but-wrong" question, now perfect), routing 100%, summary generated cleanly (coercion fix). Cross (`xp-erp-epm-flow`) groundedness ~2–3 — the known-weak category, not a regression.

The full 45×3 scorecard refresh was deferred here (~135 queries, ~$20–25, over the then-$20 cap). *(Update 2026-06-01: cap raised to $40; the full run was done — on the **60-question** set, after the rebalance below. See "Full end-to-end LLM-judge run" further down.)*

---

## Eval deepening — 4-mode ablation, dataset rebalance, reranker tuning (2026-05-31)

Turned the eval from "3 modes, 45 q (30/10/5)" into a balanced **4-mode × 4-category** ablation, and used it to *tune* the reranker. Commits: dataset `a0012e3`/`8613eaa`/`4b7f175`/`8a51916`, `keyword_only` `74c0d2f`, rerank levers `81eb3fe`, default flip `a528e14`.

**Dataset → 60 questions, balanced 15/15/15/15** (single / cross / adversarial / **exact_term**). Single trimmed to a clean spread; cross +5 (erp↔epm); adversarial +10 lures across all three target products (incl. *inverted* ones — "allocate overhead in the general ledger" → ERP, mirroring "allocation in Planning" → EPM, so the router can't memorize a keyword shortcut); exact_term = 15 lexical lookups (member names / codes / acronyms like `OEP_Working`, `XCC`, `DRG`, `NSG`) — the BM25-favorable regime. Every new question's keywords verified present in the target corpus.

**`keyword_only` (BM25) mode added** (`retrieval_eval` only, not the runner or MCP/demo surface) so the component ablation is *measured*. `exact_term` is its own category, reported on the **TEXT bar** (its tokens live in chunk body, not headings, so the section bar reads ~0 for all modes).

**The n=5 → n=15 corrections (both directions) — the methodology highlight:** at n=5, adversarial *overstated* rerank (an apparent recall@1 "doubling" 40→80%) and exact-term *understated* BM25 (keyword tied vector). At n=15 both regressed to the truth: rerank's adversarial edge is *modest* (53% vs 40%), and BM25 *edges* vector on exact-term (0.956 vs 0.906). Small samples lie in both directions — which is why the set is now balanced 15-per-category.

**Reranker tuning — two synergistic levers, now the default.** `hybrid_rerank` trailed vector on the strict bar. Made two levers env-toggleable and A/B'd them on the (free) retrieval eval: `RERANK_POOL=vector` (rerank a clean vector pool, not the noisy RRF pool) + `RERANK_INCLUDE_PATH=1` (prepend each chunk's section path to the reranker input). **Isolated them: neither alone moves it (+0.000 / +0.014), together +0.040 — super-additive.** My "RRF noise is the weak link" hypothesis was wrong; the win is the *interaction* (clean pool + structural context). Flipped both on as the default (`a528e14`, env-overridable) and rebuilt the MCP servers so the live demo uses it.

**All-regime retrieval verdict (strict bar, recall@1/MRR; exact_term on TEXT bar):** no single first-stage mode dominates — vector wins semantic (single/cross), keyword *edges* vector on exact-term, rerank wins adversarial; naive `hybrid` (RRF) underperforms everywhere. **Tuned `hybrid_rerank` wins or ties every regime** — the empirical case for the production default. Full tables in `TEST_LOG.md`.

---

## Full end-to-end LLM-judge run + routing hardening (2026-06-01)

**Cap raised to $40 → ran the full 60-q × 3-mode judged run** (`evals/runner.py`; summary `20260531T221433Z`). Docs commit `a08d02f`.

- **`hybrid_rerank` is best on judged answer quality in every category** — overall **3.97** vs vector 3.79 / hybrid 3.66; by category: single 4.36, cross 3.27, adversarial 4.22, exact_term 4.04. Stronger than the retrieval-level story: on *answer quality* (what ships) the tuned reranker wins outright.
- **Groundedness recovered to ~3.7–4.1** (rerank 4.10) — confirms the judge-snippet fix end-to-end (was an artifact-depressed ~2.4).
- **Routing: 100% on single/cross/exact_term; adversarial ~80% — but it's *hedging*, not misrouting.** Every adversarial "miss" is the correct product **plus** one extra lure-suggested product (the router never routes to only the wrong place); answers stay correct (adversarial groundedness 4.3–4.5).

**Routing hardening (`2c370d6`, partial).** Hardened the OCI/ERP/EPM scope boundaries so a cloud-infra question wrapped in finance vocab routes cleanly: OCI now *claims* storing/archiving files and isolating budget/financial data into compartments; ERP/EPM *disclaim* it (durable boundary facts, not question→answer mappings). Re-ran adversarial (`EVAL_CATEGORY=adversarial`):
- **`security-policy` lure fixed**; but **`archive-financial` (+erp) and `compartment-budget` (+epm) still hedge ~half the time** (single spot-checks landed clean — luck), and `intercompany` hedges (left alone; genuinely spans ERP+EPM).
- **Honest conclusion: prompt-based routing has a probabilistic ceiling** — hardening shifts the odds but can't make a soft prompt deterministic, and over-fitting the wording to chase the metric is what the eval exists to prevent. The residual is harmless (right product + wasteful extra; answers correct). A *hard* guarantee needs a routing classifier or a post-route relevance gate — logged as future work. Docs commit `7993651`.

---

## Docs + demo polish (2026-06-01)

- **`README.md` refreshed** (`7d07c6f`, pushed): corpus ~7,740 → **~3,100 chunks**; EPM per-module search tools; retrieval now 4 modes incl. the `keyword_only` baseline + tuned `hybrid_rerank` default + relevance floor; the **Evaluation section rewritten** (60-q 4-category, all-regime verdict, end-to-end quality 3.97, the honest findings incl. the n=5→n=15 corrections, the judge-snippet bug, and the routing-hedge ceiling); "what I'd do next" updated (routing classifier; eval-set grown).
- **"Tricky routing" UI button** (`77f80a2`, deployed): a 5th sample pool of terminology lures, all **verified to route to a single correct product**, showcasing the router resisting misleading wording. Deliberately excludes the genuinely-ambiguous intercompany lure.
- **`DEMO_SCRIPT.md` refreshed** (gitignored / local): self-correcting eval headline, the live "Tricky routing" beat, the honest routing-ceiling story, updated Q&A.

**Current corpus (post-coverage-fix):** erp 947 / epm **1424** / oci 754 = **3125** chunks (both stores reconcile). EPM grew by the *Administering Planning* guide (the coverage fix).

---

## Exact_term reframe (real regime-A set) + candidate-pool re-A/B (2026-06-01)

**Reframed `exact_term` into a true regime-A set.** The original 15 were mostly OOV/jargon (Smart View, Calc Manager, subledger, revaluation, shielded) a strong embedder handles fine — `hybrid_rerank` ceilinged at 1.000, so the category couldn't discriminate. Swapped the 5 weakest for **unique-identifier lookups with confusable siblings present in the corpus**: `OEP_Forecast` (vs OEP_Working/Plan), `VM.Standard.E4.Flex` (vs E3/E5/E6.Flex, co-listed in-chunk), and ERP table names `AP_INVOICE_LINES_ALL` / `RA_CUSTOMER_TRX_ALL` / `RA_INTERFACE_LINES_ALL` (header-vs-lines siblings). Set stays 15 (5 epm / 6 oci / 4 erp); all keywords verified in corpus.

**Result — overturns the "wins every regime" headline.** On the harder set (TEXT bar, n=15): `vector_only` exact-term falls **0.906 → 0.813** (it grabs confusable siblings), `keyword_only` holds 0.956 and **wins exact-term outright**, `hybrid_rerank` falls **1.000 → 0.922** (can't catch BM25). Honest verdict now: **no mode is strictly dominant** — reranker wins single+adversarial, vector wins cross, keyword wins exact-term; the reranker is the **robust all-rounder** (never worst in any regime), which is the real case for the default. Supersedes the earlier "only mode that wins or ties every regime."

**`RERANK_POOL` default flipped `vector` → `hybrid` and deployed.** Re-A/B'd the candidate pool on the harder set: `pool=hybrid` **loses or ties every category** (cross −0.040, adversarial −0.026, exact_term −0.011 vs `pool=vector`) — the exact chunk is already in the 30-deep vector pool (vector recall@10 = 100%), so adding BM25 only adds noise. A clean negative result reconfirming the 2026-05-31 tuning. **Shipped `pool=hybrid` anyway as a deliberate choice** (deployed to the 3 MCP servers via rebuild) — so the deployed default trails the A/B optimum; recorded honestly, not as a win. Scorecards `20260601T154138Z` (hybrid) / `154256Z` (vector). Deviation from plan: this reverses the committed `pool=vector` tuning default against the eval evidence — logged as an explicit, eyes-open decision. Answer-quality/judge figures predate this and are **pending a runner re-run**.

---

## Hybrid fusion investigation — why RRF never beats its stronger leg (2026-06-01)

Diagnosed (per-query, scorecard `154138Z`) why naive `hybrid` (RRF) lands *between* its legs everywhere: near-zero leg complementarity (keyword uniquely rescues vector in **2/75** queries), a single-target metric that rewards the best rank not consensus (better leg already #1 in **63%**; RRF dilutes **28%** vs additive-win **7%**), and rank-only/equal-weight fusion. Added env-toggleable score-based `weighted` fusion and a per-query `adaptive` router (identifier-like query → BM25-leaning α, else vector-leaning) in `shared/retrieval.py` (default stays `rrf`; offline self-tested). **Adaptive at sem=1.0 Pareto-dominates pure vector** — ties all three semantic regimes, **+0.143 MRR on exact_term** (0.956 = keyword ceiling), zero regressions. Full ladder + caveats in `TEST_LOG.md`. Production `hybrid_rerank` reranks the candidate pool regardless, so this is primarily an analysis/eval result, not a live-answer change.
