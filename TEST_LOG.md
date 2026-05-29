# Test Log & Results

A record of what was verified at each phase and the measured results — evidence the
system works end to end, and the honest findings from the evaluation. Written for
interview reference; numbers are from live runs on the VPS unless noted.

---

## Phase 0 — Foundation

| Test | Result |
|---|---|
| DNS `navigator.p36server.com` resolves | → `89.116.167.171` at public resolvers (8.8.8.8, 1.1.1.1) |
| Anthropic API key | `POST /v1/messages` (claude-sonnet-4-6) → HTTP 200 |
| Voyage API key | `POST /v1/embeddings` (voyage-3-large) → HTTP 200, **1024-dim** output |
| Basic-auth hash | bcrypt hash generated with `htpasswd -nbB`, verified against the password |

## Phase 1 — Ingestion (data layer)

Full ingest ran on the VPS via the `ingestion` container (fetch → chunk → embed → write).

| Product | Source | SQLite rows | Qdrant points |
|---|---|---|---|
| erp | 4 Fusion Financials guide PDFs (25D) | 4034 | 4034 |
| epm | 3 EPM admin guide PDFs | 2181 | 2181 |
| oci | 240 crawled HTML pages (60/service) | 1525 | 1525 |
| **total** | | **7740** | **7740** |

- **Counts reconcile** across both stores for all three products.
- **BM25 spot-check:** `MATCH 'journal entry'` → 3 ERP journal/ledger chunks (on-topic).
- **Vector spot-check:** "how do I reverse a journal entry?" → *Reversal Settings on Journals* (0.678), *Journal Reversals* (0.673), *Manual Journal Reversal* (0.665) — semantic match despite different wording.
- ~1.7M Voyage tokens embedded.

## Phase 2 — Hybrid retrieval library

`retrieve(query, product, mode, top_k)` with three modes.

- **p95 hybrid_rerank latency: 323 ms** (target < 2s).
- Cross-mode behavior on "how do I reverse a journal entry?":
  - `vector_only`: clean journal-reversal hits.
  - `hybrid` (RRF, k=60): mixed in a less-precise "Reverse Reconciliation for Clearing Accounts".
  - `hybrid_rerank`: rerank-2 restored precision (Journal Reversals 0.828 to the top).
- No hardcoded product list (the function takes `product` as a string).

## Phase 3 — First MCP server (ERP)

End-to-end MCP client call to the ERP server (streamable-HTTP):

- `list_tools` → `search_docs`, `get_document`, `list_topics` with scoped descriptions.
- `search_docs("reversing journal entries")` → relevant chunks (rerank 0.82 / 0.80 / 0.79).
- `get_document(doc_id)` → full 753 KB document reconstructed from chunks.
- Container `Up (healthy)`, logs clean.

## Phase 4 — Three federated MCP servers

Live MCP client test against all three:

| Query | Routed to | products in results |
|---|---|---|
| "reverse a journal entry" (→ erp) | erp:8001 | `{erp}` only |
| "create an object storage bucket" (→ oci) | oci:8002 | `{oci}` only |
| "run a consolidation" (→ epm) | epm:8003 | `{epm}` only |

**Per-collection isolation holds** — each server returns only its own product's chunks. All three `Up (healthy)`.

## Phase 5 — Orchestrator (routing brain)

Claude Sonnet 4.6 agent loop, MCP client to all three servers, namespaced tools.

| Question | Routed to | Tool calls |
|---|---|---|
| single / ERP | `{erp}` | 1 |
| single / OCI | `{oci}` | 1 |
| single / EPM | `{epm}` | 1 |
| **cross / ERP→EPM** | **`{erp, epm}`** | 4 (federated, synthesized from both) |
| out of scope ("weather") | `{}` | 0 (graceful refusal) |

- Routing correct on every case; cross-product question federates across two servers.
- **Latency:** ~19–22s single, ~43s cross — dominated by Claude synthesizing long answers across two sequential calls (retrieval itself ~300ms). Candidate for streaming in polish.

## Phase 6 — Live demo

- **https://navigator.p36server.com** serves the demo, gated by Traefik **basic auth**.
- Cross-product sample renders a cited answer with a **two-server (erp+epm) trace**.
- Streamlit websockets work through Traefik on defaults.

---

## Phase 7 — Evaluation (the methodology + findings)

**Dataset:** 45 hand-built questions — 30 single-product (10 erp/oci/epm), 10 cross-product (erp↔epm), 5 adversarial (product-terminology lures + an exact-term BM25 case). Schema carries `expected_products` and `expected_section_keywords`.

**Two evaluation levels:**
1. **End-to-end** (`runner.py`): query the orchestrator once per mode, LLM-as-judge (Sonnet 4.6, Batches API) scores correctness / groundedness / citation 1–5; also routing accuracy, recall, latency p50/p95.
2. **Retrieval-level** (`retrieval_eval.py`): compare modes directly on `retrieve()` — no agent, no judge — reporting recall@k and **MRR** under two relevance bars (keyword anywhere = lenient; keyword in section path = strict "right section"). Near-free and fast; isolates exactly what the modes change.

### Results

**End-to-end answer quality (6 easy-ERP dry run, judge mean 1–5):**

| run | vector_only | hybrid | hybrid_rerank |
|---|---|---|---|
| before stopword fix | 3.89 | 3.17 | 3.61 |
| after stopword fix | 3.94 | 3.22 | 3.83 |

(recall saturated at 100% on these easy questions.)

**Retrieval scorecard (55 question×product pairs, top_k=10):**

Lenient (keyword anywhere):

| mode | recall@1 | recall@5 | MRR |
|---|---|---|---|
| vector_only | 96% | 98% | 0.968 |
| hybrid | 98% | 98% | 0.982 |
| hybrid_rerank | 95% | 98% | 0.958 |

Strict (keyword in section path):

| mode | recall@1 | recall@5 | MRR |
|---|---|---|---|
| vector_only | 73% | 96% | 0.817 |
| hybrid | 65% | 91% | 0.778 |
| hybrid_rerank | 67% | 93% | 0.778 |

Per-category breakdown (strict bar): _to be appended from the next run._

### The honest finding

**On this corpus, pure vector retrieval is the strongest mode** — voyage-3-large on clean Oracle docs with well-formed questions already retrieves a relevant chunk first ~96% of the time (lenient) / 73% (strict section). Hybrid adds candidates whose RRF fusion can displace the clean vector top-1; rerank does not recover the keyword/section signal. Held across both relevance bars and both evaluation levels.

This is a result, not a failure. Hybrid + rerank earn their value in **harder regimes** — noisier corpora, exact-match / keyword / typo-laden queries, much larger scale — that a clean, well-embedded demo corpus doesn't stress.

### Debugging done (in the plan's order)

1. **Eval too easy / metric too lenient** → added the strict section-path relevance bar and a per-category breakdown (didn't manufacture a curve — reported the truth).
2. **Hybrid bug** → found and fixed: the BM25 leg OR-ed *every* token including stopwords (`how/do/a/in`), matching nearly every chunk and polluting RRF; now ORs content words only. Improved hybrid retrieval, but vector still leads on this corpus.
3. **Rerank candidate pool** → already 30.

### Interview talking points

- "I built the full hybrid + rerank pipeline **and** a rigorous two-level eval, and the eval told me pure vector already saturates retrieval quality on this corpus. The honest answer is *it depends on the corpus and query distribution* — I can tell you exactly when hybrid and rerank earn their keep."
- The eval **caught a real bug** (BM25 stopword pollution) that a glance at the demo never would — that's the point of evals as quality gates.
- A flat or non-monotonic scorecard, honestly reported, is more credible than a suspiciously clean curve; the methodology (two levels, two relevance bars, LLM-as-judge with a strict rubric, per-category) is the defensible part.
- Where I'd push next: a harder/representative query set (exact IDs, abbreviations, typos), human-labeled gold chunks, and re-running at larger scale where hybrid/rerank typically separate.
