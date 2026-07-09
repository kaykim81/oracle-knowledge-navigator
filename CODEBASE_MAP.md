# Codebase Map

Where everything lives and what each file does. Companion to `ARCHITECTURE.md`
(how the pieces fit together at runtime) and `MASTER_PLAN.md` (the build
sequence). This is the "what is this file" reference.

The stack is a federation of per-product MCP servers behind a routing
orchestrator, with a shared retrieval library, a Streamlit UI, and offline
ingestion + eval tooling. Each container bakes in a copy of `shared/`.

---

## Root

| File | What it is |
|------|------------|
| `CLAUDE.md` | Standing rules for working in this repo (phase discipline, locked tech stack, environment notes). Read by Claude Code on every session. |
| `PROJECT_CONTEXT.md` | The why: interview-demo framing, the role, what the project is meant to prove. |
| `MASTER_PLAN.md` | The build sequence — phases, numbered steps, and each phase's "definition of done." The source of truth for build order. |
| `ARCHITECTURE.md` | Runtime topology: containers, networks, data flow, the per-collection isolation boundary. |
| `DEVELOP_LOG.md` | Running log of what's been built, deferred, or deviated from — updated at the end of each phase. |
| `README.md` | Public-facing overview and quickstart. |
| `ACRONYM.md` | Oracle domain glossary (ERP/EPM/OCI terms) used to ground the corpus and eval questions. |
| `DEMO_SCRIPT.md` | Walk-through for demoing the live system. |
| `STORY.md` | Interview prep / narrative notes (local only — gitignored). |
| `TEST_LOG.md` | Manual verification log — commands run and outcomes observed. |
| `docker-compose.yml` | The whole stack: qdrant, three MCP servers, orchestrator, UI, plus Traefik labels for ingress. |
| `.env` / `.env.example` | API keys (Anthropic, Voyage) and config. `.env` is gitignored; `.env.example` holds placeholders. |
| `.dockerignore` | Excludes data/venv/artifacts from build contexts. |
| `navigator-data-*.tar.gz` | Snapshots of the ingested data volume (Qdrant + SQLite) for transport to the VPS. |

---

## `shared/` — the common library

Copied into each MCP server, the orchestrator, and the ingestion/eval images at
build time. Every non-trivial module has a `python -m shared.<module>` smoke
test.

| File | What it is |
|------|------------|
| `models.py` | Core Pydantic models that flow through the pipeline: `Document` → `Chunk` → `SearchResult`. Defines the product list. |
| `chunking.py` | Structure-aware chunking. `chunk_html` / `chunk_text` split on heading boundaries (preserving `section_path`) and pack paragraphs toward ~800 tokens via `chunk_blocks`. |
| `embeddings.py` | Voyage embedding client — batched (128/req), retry-with-backoff, pinned to 1024-d. Used by ingestion (`document`) and retrieval (`query`). |
| `qdrant_store.py` | Qdrant vector store: client, collection init, upsert, vector search. One collection per product (`{product}_docs`); per-collection isolation is the enforcement boundary. |
| `db.py` | SQLite storage + FTS5 (BM25) keyword index. `chunks` table mirrors the `Chunk` model; `chunks_fts` kept in sync by triggers. Idempotent upserts on deterministic chunk IDs. |
| `retrieval.py` | Hybrid retrieval engine. One `retrieve()` entry point, three modes: `vector_only`, `hybrid` (RRF fusion), `hybrid_rerank` (Voyage rerank-2). The engine all MCP servers call. |
| `mcp_server.py` | Factory that builds a per-product FastMCP server exposing `search_docs` / `get_document` / `list_topics`, scoped to one product. Keeps each `mcp_servers/<product>/server.py` thin. |

---

## `mcp_servers/` — the federated knowledge servers

One server per Oracle product line; each is a thin config over
`shared.mcp_server` with its own Dockerfile + requirements. Each owns only its
own Qdrant collection.

| Path | What it is |
|------|------------|
| `erp/server.py` | ERP (Fusion Cloud Financials: GL, AP, AR, Fixed Assets). Listens on :8001. |
| `oci/server.py` | OCI (Compute, Networking, Object Storage, IAM). Listens on :8002. |
| `epm/server.py` | EPM (Fusion Cloud EPM suite). Listens on :8003. Exposes **one module-scoped search tool per module** instead of a single `search_docs`, to stop cross-module bleed within the shared collection. |
| `<product>/Dockerfile`, `requirements.txt` | Per-server image build + deps. |

---

## `orchestrator/` — the routing brain

| File | What it is |
|------|------------|
| `agent.py` | Connects to the three MCP servers as a client, namespaces their tools per product, and runs a Claude (Sonnet 4.6) tool-use loop that decides which product(s) to query. `query()` returns `{answer, trace, latency_ms, cost}`. Anthropic SDK directly, with prompt caching. |
| `server.py` | FastAPI wrapper: `POST /query` (JSON, used by eval), `POST /query/stream` (SSE, used by UI), `/health`. Discovers MCP tools once on startup. |
| `prompts/system.md` | The orchestrator's system prompt — routing instructions and citation rules. |
| `Dockerfile`, `requirements.txt` | Image build + deps. |

---

## `ui/` — the demo front end

| File | What it is |
|------|------------|
| `app.py` | Streamlit single-page app: ask a question, see the cited answer plus the live agent trace (which servers were called, top chunks, per-step latency). Streams SSE from the orchestrator. The only publicly exposed service. |
| `Dockerfile`, `requirements.txt` | Image build + deps. |

---

## `ingestion/` — offline corpus pipeline

| Path | What it is |
|------|------------|
| `fetch_docs.py` | Downloads raw Oracle docs per `sources/_index.json`. Two modes: `pdf` (ERP, EPM) and `crawl_html` (OCI, BFS under a section prefix). Writes `_fetched.json` per product. |
| `ingest.py` | Fetched files → chunks → Voyage embeddings → both stores (SQLite/FTS5 + Qdrant). Idempotent via deterministic chunk IDs. |
| `sources/_index.json` | The corpus manifest — which guides/services to fetch per product. |
| `sources/{erp,epm,oci}/` | Downloaded raw docs + `_fetched.json` (gitignored). |
| `Dockerfile`, `requirements.txt` | Image build + deps. |

---

## `evals/` — measurement

| Path | What it is |
|------|------------|
| `dataset.jsonl` | The eval question set (question + expected product/section). |
| `retrieval_eval.py` | Retrieval-level scorecard — compares the three modes directly on `retrieve()`, bypassing the agent and judge. Near-free, fast; isolates ranking quality (rank-of-first-relevant under TEXT and SECTION bars). |
| `runner.py` | End-to-end runner — queries the orchestrator once per mode per question, judges answers, writes the per-mode comparison table. Produces `{ts}.jsonl` + `{ts}_summary.md`. |
| `judge.py` | LLM-as-judge — scores (question, answer, chunks) 1–5 on correctness/groundedness/citation via Claude over the Batches API. |
| `results/*.md` | Committed scorecards — `_retrieval.md` (retrieval-level) and `_summary.md` (end-to-end), timestamped. The headline artifacts. |
| `Dockerfile`, `requirements.txt` | Image build + deps. |

---

## `data/` — runtime state (gitignored, lives on the volume)

| Path | What it is |
|------|------------|
| `qdrant/` | Qdrant's on-disk collections + raft state. |
| `sqlite/chunks.db` | The SQLite chunks DB + FTS5 index. |

---

## Other

| Path | What it is |
|------|------------|
| `.devcontainer/` | Dev container definition (this container runs on the VPS, sharing the project dir). |
| `.venv/` | Local Python env for dev-container smoke tests (gitignored). |
