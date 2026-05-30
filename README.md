# Oracle Knowledge Navigator

A federated, agentic RAG system over three Oracle product knowledge bases — **ERP** (Fusion Financials), **OCI** (Cloud Infrastructure), and **EPM** — exposed as independent [MCP](https://modelcontextprotocol.io) servers behind one orchestrating agent. Ask a question in plain English; the agent routes it to the right knowledge base(s), retrieves with a hybrid pipeline (vector + BM25 + rerank), and answers with citations. A single question can span products.

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
                            │  HTTP POST /query
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
- Each **MCP server** is product-scoped and identical in shape (one factory, `shared/mcp_server.py`); only its corpus, scope description, and port differ. Per-collection isolation means a server only ever returns its own product's chunks.
- **Retrieval** (`shared/retrieval.py`) has three modes: `vector_only` (Qdrant, voyage-3-large), `hybrid` (vector + SQLite FTS5 BM25 fused with Reciprocal Rank Fusion), and `hybrid_rerank` (hybrid candidates reranked with Voyage rerank-2).
- Only the **UI** is public (joined to both the internal network and Traefik's); everything else stays on an internal Docker network.

## Quick start

Runs anywhere Docker Compose runs. You supply Anthropic + Voyage API keys.

```bash
git clone https://github.com/kaykim81/oracle-knowledge-navigator.git
cd oracle-knowledge-navigator

cp .env.example .env          # then edit: add ANTHROPIC_API_KEY and VOYAGE_API_KEY
                              # (the Traefik/basic-auth vars are only needed for public deploy)

docker compose up -d --build  # qdrant, 3 MCP servers, orchestrator, UI
docker compose run --rm ingestion   # one-time: fetch → chunk → embed → write (~7,740 chunks)
```

The UI is on port 8501 (behind Traefik in production; map it locally to browse directly). Smoke-test the agent without the UI:

```bash
docker compose exec orchestrator python -m orchestrator.agent \
  --question "How does data flow from Fusion ERP into EPM consolidation?"
```

Most modules also have a CLI smoke test (e.g. `python -m shared.retrieval --help`). For a public deployment, the `ui` service's Traefik labels in [docker-compose.yml](docker-compose.yml) handle TLS and basic auth — set `PUBLIC_HOSTNAME`, `BASIC_AUTH_USER`, and `BASIC_AUTH_PASSWORD_HASH` in `.env` (see [.env.example](.env.example)) and attach to your existing Traefik network.

## Evaluation

The eval is the part I'd most want to defend in the interview, so it's deliberately rigorous and honest. Full methodology and findings: [TEST_LOG.md](TEST_LOG.md). Two levels:

1. **Retrieval-level** (`evals/retrieval_eval.py`) — compares the three modes directly on `retrieve()`, no agent, no judge. Reports recall@k and MRR under two relevance bars (keyword anywhere = lenient; keyword in the *section path* = strict). Near-free, isolates exactly what the modes change.
2. **End-to-end** (`evals/runner.py`) — queries the orchestrator once per mode and scores answers with an LLM-as-judge (Sonnet 4.6 over the Batches API) on correctness / groundedness / citation, plus routing accuracy and latency.

Dataset: 45 hand-built questions — 30 single-product, 10 cross-product (ERP↔EPM), 5 adversarial (terminology lures).

**The honest headline: mode value is input-dependent, and the aggregate hides it.** Broken out by category (retrieval, strict section bar — recall@1 / MRR):

| category (n)      | vector_only   | hybrid            | hybrid_rerank     |
|-------------------|---------------|-------------------|-------------------|
| single (30)       | 87% / 0.933   | 70% / 0.833       | 87% / **0.933**   |
| cross (20)        | 60% / 0.714   | **65% / 0.757**   | 35% / 0.540       |
| adversarial (5)   | 40% / 0.533   | 40% / 0.567       | **80% / 0.800**   |

- **Adversarial → rerank wins decisively** (recall@1 doubles, 40% → 80%): when terminology misleads, the reranker's semantic precision surfaces the right section.
- **Cross-product → hybrid wins** (MRR 0.757 > 0.714): the BM25 leg catches specific cross-domain sections.
- **Easy single-product → vector already saturates**; hybrid's extra candidates add RRF noise, rerank cleans it back to vector's level.

End-to-end, **rerank beats hybrid on judged answer quality in every category** (overall 3.45 vs 3.14 / 5). The eval also caught a real bug (BM25 was OR-ing stopwords, dragging hybrid below vector) and a real limitation: routing was 100% accurate on normal questions but only **20% on adversarial lures** — a router weakness, not a retrieval one (retrieval is strong *given correct routing*). I then **hardened the router with context engineering** — sharper tool/scope descriptions encoding the real Oracle product boundaries — which took **adversarial routing from 20% → 100%**, with retrieval metrics unchanged (the scope edits only affect what the router reads, not `retrieve()`). Details and methodology in [TEST_LOG.md](TEST_LOG.md).

## What I'd do next

- **Push routing robustness further.** Context-engineering the scope descriptions took adversarial routing 20% → 100% on the current lures; next is a larger, harder adversarial set and an explicit disambiguation/confidence step so the router degrades gracefully on lures it hasn't seen.
- **Streaming responses.** End-to-end p50 is ~30s, dominated by answer synthesis (retrieval is ~300ms). Streaming the answer token-by-token in the UI makes the demo feel dramatically faster.
- **Oracle 23ai migration.** Consolidate Qdrant + SQLite into Oracle Database 23ai's native vector search — one store, one product family, the obvious enterprise fit.
- **Tenant isolation.** Add a `tenant_id` dimension to chunks and filter every retrieval by it — the multi-tenancy primitive an Accenture deployment needs.
- **Scale the federation** from 3 to N servers (the pattern is already a factory + a registry) and **grow the eval set** (adversarial/cross samples are small — n=5/20 here).
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

Build history and per-phase decisions: [PROGRESS.md](PROGRESS.md). 
Per-phase verification and eval findings: [TEST_LOG.md](TEST_LOG.md).
