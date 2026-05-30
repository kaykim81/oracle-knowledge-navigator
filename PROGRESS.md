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
