# Oracle Knowledge Navigator

A federated, agentic RAG system over three Oracle product knowledge bases — **ERP** (financials and operations), **OCI** (cloud infrastructure), and **EPM** (planning and analysis) — exposed as independent [MCP](https://modelcontextprotocol.io) servers behind one orchestrating agent. Ask a question in plain English; the agent routes it to the right knowledge base(s), retrieves with a hybrid pipeline (vector + BM25 + rerank), and answers with citations. A single question can span products.

> Built as an interview demo for an **Applied AI Engineer** role on Accenture's **Oracle Business Group AI Center of Excellence**. It's a scaled-down, end-to-end proof of the pattern that practice runs at scale — many product-scoped knowledge servers, one agent federating across them, with retrieval quality measured and the whole thing deployed behind a real URL.

**Live demo:** https://navigator.p36server.com (basic-auth protected — credentials shared by email, not here).

---

## The problem

Enterprise Oracle knowledge is fragmented across product lines — each with its own documentation, terminology, and audience. A user with a question ("how does data flow from ERP into EPM consolidation?") shouldn't have to know *which* knowledge base holds the answer, and a single team can't hand-tune one monolithic index across dozens of unrelated domains. The scalable shape is **federation**: each product line is its own retrieval service, and an agent decides — per question — which one(s) to consult and how to synthesize across them. This demo implements that shape end to end for three products, the same way it would extend to 27.

## Architecture

```
                          Internet
                             │
                             ▼
                    ┌────────────────┐
                    │  Traefik (TLS) │   existing, shared
                    └───────┬────────┘
                            │  Host(navigator.p36server.com) + basic auth
                            ▼
                    ┌────────────────┐
                    │   Streamlit UI │   (the only public service)
                    └───────┬────────┘
                            │  POST /query/stream (SSE: live trace + streamed answer)
                            ▼
                    ┌────────────────┐
                    │  Orchestrator  │   FastAPI + Claude agent loop (MCP client)
                    └───────┬────────┘
         ┌──────────────────┼──────────────────┐
         │ MCP              │ MCP              │ MCP        (streamable-HTTP)
         ▼                  ▼                  ▼
   ┌──────────┐       ┌──────────┐       ┌──────────┐
   │ ERP MCP  │       │ OCI MCP  │       │ EPM MCP  │       each: search_docs /
   └────┬─────┘       └────┬─────┘       └────┬─────┘             get_document /
        │                  │                  │                   list_topics
        └──────────────────┼──────────────────┘
                           ▼
                  ┌──────────────────┐
                  │ Qdrant + SQLite  │   shared stores (vectors + BM25)
                  └──────────────────┘
```

- The **orchestrator** connects to all three MCP servers, namespaces their tools (`erp_search_docs`, `oci_search_docs`, …), and runs a Claude tool-use loop: Claude picks the product(s), the orchestrator forwards each call, and the loop ends with a cited answer. The answer **streams token-by-token** over SSE while every tool call is captured in a **trace** the UI renders — federation made visible — alongside a **per-question cost panel** (Claude spend + cache-hit %; Voyage retrieval cost excluded).
- Each **MCP server** is product-scoped and identical in shape (one factory, `shared/mcp_server.py`); only its corpus, scope description, and port differ. Per-collection isolation means a server only ever returns its own product's chunks. EPM goes one level finer: its one collection holds three distinct module guides (Planning, Financial Consolidation & Close, Narrative Reporting), so it exposes **per-module search tools** (`epm_search_planning` / `_fcc` / `_narrative`, each scoped by source doc) — a Planning question structurally can't surface FCC chunks.
- **Retrieval** (`shared/retrieval.py`) modes: `vector_only` (Qdrant, voyage-3-large), `hybrid` (vector + SQLite FTS5 BM25, fused per-query by **adaptive** weighting — vector for semantic queries, BM25 for exact-token queries; RRF and fixed-weight fusion stay env-selectable), `hybrid_rerank` (the **production default** — candidates from that adaptive vector+BM25 pool reranked with Voyage rerank-2, with each chunk's section path fed to the reranker), and `keyword_only` (pure BM25, an eval-only baseline for the component ablation). A relevance floor lets the agent abstain when nothing scores well rather than answer from off-topic chunks.
- Only the **UI** is public (joined to both the internal network and Traefik's); everything else stays on an internal Docker network.

## Quick start

Runs anywhere Docker Compose runs. You supply Anthropic + Voyage API keys.

```bash
git clone https://github.com/kaykim81/oracle-knowledge-navigator.git
cd oracle-knowledge-navigator

cp .env.example .env          # then edit: add ANTHROPIC_API_KEY and VOYAGE_API_KEY
                              # (the Traefik/basic-auth vars are only needed for public deploy)

docker compose up -d --build  # qdrant, 3 MCP servers, orchestrator, UI
docker compose run --rm ingestion   # one-time: fetch → chunk → embed → write (~3,100 chunks)
```

The UI is on port 8501 (behind Traefik in production; map it locally to browse directly). Smoke-test the agent without the UI:

```bash
docker compose exec orchestrator python -m orchestrator.agent \
  --question "How does data flow from Fusion ERP into EPM consolidation?"
# add --stream to watch the trace build and the answer stream token-by-token;
# either way it prints the per-question Claude cost at the end.
```

Most modules also have a CLI smoke test (e.g. `python -m shared.retrieval --help`). For a public deployment, the `ui` service's Traefik labels in [docker-compose.yml](docker-compose.yml) handle TLS and basic auth — set `PUBLIC_HOSTNAME`, `BASIC_AUTH_USER`, and `BASIC_AUTH_PASSWORD_HASH` in `.env` (see [.env.example](.env.example)) and attach to your existing Traefik network.

## Evaluation

The eval is the part I'd most want to defend in the interview, so it's deliberately rigorous and honest. Full methodology and findings: [TEST_LOG.md](TEST_LOG.md). Two levels:

1. **Retrieval-level** (`evals/retrieval_eval.py`) — compares the retrieval modes directly on `retrieve()`, no agent, no judge. Reports recall@k and MRR under two relevance bars (keyword anywhere = lenient; keyword in the *section path* = strict). Near-free, isolates exactly what the modes change.
2. **End-to-end** (`evals/runner.py`) — queries the orchestrator once per mode and scores answers with an LLM-as-judge (Sonnet 4.6 over the Batches API) on correctness / groundedness / citation, plus routing accuracy and latency.

Dataset: **60 hand-built questions, balanced 15/15/15/15** — single-product, cross-product (ERP↔EPM), adversarial (terminology lures), and exact-term (member names / codes / acronyms — the BM25-favorable regime). The `retrieval_eval` adds a `keyword_only` (BM25) mode so the component ablation is *measured*, not inferred.

**The honest headline: no single retriever wins everywhere — it's regime-dependent, and no mode is strictly dominant.** vector owns semantic cross-product, **keyword owns exact-term** (the regime-A lexical case), and the reranker owns adversarial and ties single — so the reranker ships as the default because it is the most robust **all-rounder** (never the worst in any regime), *not* because it wins everywhere. Retrieval, by category (strict section bar; exact-term on the TEXT bar, since its tokens live in body text not headings; `hybrid` = the default per-query **adaptive** fusion, `hybrid_rerank` = the deployed default (adaptive pool + rerank) — recall@1 / MRR):

| category (n)        | keyword_only  | vector_only     | hybrid        | hybrid_rerank   |
|---------------------|---------------|-----------------|---------------|-----------------|
| single (15)         | 60% / 0.673   | 67% / 0.744     | 67% / 0.744   | **67% / 0.747** |
| cross (30)          | 30% / 0.416   | **43% / 0.569** | **43% / 0.569** | 40% / 0.514   |
| adversarial (15)    | 33% / 0.436   | 40% / 0.539     | 40% / 0.539   | **53% / 0.600** |
| exact_term (15)     | **93% / 0.956** | 73% / 0.813   | **93% / 0.956** | 87% / 0.922   |

- **Semantic (single/cross) → vector wins**, BM25 weakest.
- **Exact-term (regime A) → BM25 wins outright** (keyword 0.956 vs vector 0.813): on unique identifiers — `VM.Standard.E4.Flex`, `AP_INVOICE_LINES_ALL`, `OEP_Forecast` — the embedder grabs a *confusable sibling*; BM25 matches the exact token. Vector is the **worst** first-stage here, and even the reranker (0.922) trails keyword.
- **Adversarial → rerank wins** — semantic precision cuts through terminology lures.
- **The default `hybrid` now uses per-query *adaptive* fusion** — it routes semantic queries to vector and exact-token queries to BM25, so it **matches the best leg in every regime** (ties vector on single/cross, ties keyword on exact-term). Naive equal-weight RRF, by contrast, diluted *below* the stronger leg everywhere (see TEST_LOG).
- **`hybrid_rerank` (production default) reranks that adaptive sparse+dense pool** — it wins adversarial (0.600) and edges single, and the adaptive pool recovers the cross MRR a naive RRF pool diluted (0.475 → 0.514). Its strongest case is **answer quality and set-recall** (multi-doc eval: recall@10 0.791 / nDCG 0.701, best of all modes), not the single-target section bar, where it ~ties vector.

End-to-end (LLM-judged), `hybrid_rerank` led answer quality in every category — overall 3.97 vs vector 3.79 / hybrid 3.66 (out of 5), groundedness ~4.0+. *(These figures predate the 2026-06-01 exact_term reframe + `pool=hybrid` flip and are pending a judge re-run against the new questions.)*

**Findings the eval surfaced — the honest part:**
- **A small-sample trap, both directions.** An n=5 pilot *overstated* rerank's adversarial edge (an apparent "doubling") **and** *understated* BM25's exact-term edge — both corrected once each category grew to n=15. (That's why the set is balanced 15/15/15/15.)
- **Real bugs caught by the eval, not by eyeballing:** BM25 was OR-ing stopwords (dragging hybrid below vector); the LLM judge scored a 200-char snippet, under-measuring groundedness on larger chunks (it now sees full text).
- **Routing is 100% on single/cross/exact-term but *hedges* on some adversarial lures** — it queries the right product *plus* a finance-vocab-suggested extra. Context-engineering the scope boundaries (encoding real Oracle product lines) fixed several, but the residual is intermittent and **harmless** (right product + extra; answers stay correct, groundedness 4.3+). That's prompt routing's probabilistic ceiling — a hard guarantee would need a routing classifier, not more prompt wording.

Full methodology, the per-run scorecards, and the reasoning behind each correction are in [TEST_LOG.md](TEST_LOG.md).

## What I'd do next

- **A routing classifier for a hard guarantee.** Context-engineering the scope descriptions fixed the original lures and several new ones, but prompt routing has a *probabilistic ceiling* — it still occasionally *hedges* (queries the right product plus a finance-vocab-suggested extra) on the strongest lures. A small routing classifier or a post-route relevance gate would give a hard guarantee that prompt wording can't.
- **Cut answer-synthesis latency.** Streaming already makes the demo *feel* fast (p50 ~30s is dominated by synthesis, not retrieval's ~300ms); next is capping answer length and parallelizing the two sequential calls a cross-product question makes.
- **Oracle 23ai migration.** Consolidate Qdrant + SQLite into Oracle Database 23ai's native vector search — one store, one product family, the obvious enterprise fit.
- **Tenant isolation.** Add a `tenant_id` dimension to chunks and filter every retrieval by it — the multi-tenancy primitive an Accenture deployment needs.
- **Scale the federation** from 3 to N servers (the pattern is already a factory + a registry). The eval set is now a balanced 60 questions across 4 regimes; the next lift is human-labeled gold chunks and a larger adversarial set.
- **Query decomposition** for multi-hop cross-product questions — break a compound question into sub-questions before retrieving.

## Tech stack

| Choice | Why |
|---|---|
| **Claude Sonnet 4.6** via the Anthropic SDK directly | Strong tool-use/agentic reasoning; SDK directly (no LangChain/LangGraph) keeps the agent loop explicit and debuggable. |
| **MCP** (official SDK, streamable-HTTP) | Each knowledge base is a standard, independently deployable tool server — the federation primitive, and the literal protocol the role centers on. |
| **Qdrant** | Fast, self-hostable vector DB; one collection per product gives clean isolation. |
| **voyage-3-large** (1024-dim) embeddings + **rerank-2** | Top-tier retrieval quality; the reranker is what makes the hybrid candidate set pay off on hard queries. |
| **SQLite + FTS5** | Zero-ops BM25 lexical search in-process; the keyword leg of hybrid retrieval. |
| **Reciprocal Rank Fusion** | Score-free way to fuse vector + BM25 rankings; robust without tuning per-source score scales. |
| **FastAPI** (orchestrator) + **Streamlit** (UI) | Minimal, well-understood; the trace-rendering UI is the demo's centerpiece. |
| **Anthropic Batches API** for LLM-as-judge | Half-price, higher-throughput scoring — appropriate for offline eval. |
| **Docker Compose** behind existing **Traefik** | Reproducible multi-service stack; Traefik handles TLS + basic auth without touching its config. |

## Repository layout

```
shared/         models, chunking, SQLite/FTS5, Qdrant store, retrieval, MCP server factory
ingestion/      fetch → chunk → embed → write pipeline (run as a one-shot container)
mcp_servers/    erp / oci / epm — thin per-product config over the factory
orchestrator/   FastAPI + Claude agent loop (MCP client), system prompt
ui/             Streamlit single-page app (answer + agent trace)
evals/          dataset, retrieval scorecard, end-to-end runner, LLM judge
```

Build history and per-phase decisions: [DEVELOP_LOG.md](DEVELOP_LOG.md).  
Per-phase verification and eval findings: [TEST_LOG.md](TEST_LOG.md).

