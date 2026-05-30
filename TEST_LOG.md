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

**End-to-end (full run, 45 questions × 3 modes, LLM-judged):**

Overall:

| mode | routing acc | recall | quality (1–5) | latency p50 | p95 |
|---|---|---|---|---|---|
| vector_only | 91% | 98% | 3.45 | 31s | 100s |
| hybrid | 91% | 98% | 3.14 | 33s | 121s |
| hybrid_rerank | 91% | 100% | **3.45** | 28s | 76s |

Judged quality by category (mean 1–5) — **rerank > hybrid in every category**:

| category | vector_only | hybrid | hybrid_rerank |
|---|---|---|---|
| single (28) | 3.60 | 3.31 | **3.71** |
| cross (10) | 3.33 | 2.93 | 3.00 |
| adversarial (5) | 2.87 | 2.60 | 2.80 |

Routing accuracy by category: single **100%**, cross **100%**, adversarial **20%**.

(Earlier 6-question dry run showed the same direction; the BM25 stopword fix lifted hybrid 3.17 → 3.22 on it.)

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
| hybrid | 65% | 91% | 0.781 |
| hybrid_rerank | 67% | 93% | 0.778 |

**Per-category breakdown (strict section bar) — recall@1 / MRR:**

| category (n) | vector_only | hybrid | hybrid_rerank |
|---|---|---|---|
| single (30) | 87% / 0.933 | 70% / 0.833 | 87% / **0.933** |
| cross (20) | 60% / 0.714 | **65% / 0.757** | 35% / 0.540 |
| adversarial (5) | 40% / 0.533 | 40% / 0.567 | **80% / 0.800** |

### The honest finding

**Mode value is input-dependent — each mode wins in the regime it was designed for**, and the aggregate (dominated by 30 easy single-product questions where vector already saturates) masks it:

- **Adversarial questions → rerank wins decisively: recall@1 doubles, 40% → 80%.** When terminology is misleading (e.g. "reverse a *consolidation* journal" lures ERP but the answer is EPM), rerank's semantic precision surfaces the right section. This is exactly what a reranker is for.
- **Cross-product questions → hybrid wins** (MRR 0.757 > vector 0.714): the BM25 keyword leg catches the specific cross-domain sections; rerank over-reorders on the mixed-domain query and dips.
- **Easy single-product → vector ≈ rerank > hybrid**: voyage-3-large already retrieves the right section first ~87% of the time; hybrid's keyword candidates add RRF noise; rerank cleans it back to vector's level.

So both DoD criteria hold *in the right regime* — hybrid beats vector on recall for cross-product, and **rerank beats hybrid on judged answer quality in every category** (overall 3.45 vs 3.14; single 3.71 vs 3.31). Hybrid + rerank earn their keep most on **harder inputs**; on a clean, well-embedded corpus with well-formed questions, pure vector is a strong baseline. (Caveats: adversarial n=5, cross n=20 — modest samples; directional.)

### The routing finding (end-to-end)

Routing accuracy is **100% on single-product and cross-product** questions but **20% on adversarial** ones: the orchestrator is **fooled by terminology lures** — "reverse a *consolidation* journal" routes to ERP (the word "journal") instead of EPM; "compartments for *financial data*" routes to ERP instead of OCI IAM. This is the cause of the low *end-to-end* adversarial quality across all three modes — it's a **routing miss, not a retrieval miss**. The retrieval scorecard (which forces the correct product) shows rerank doubling adversarial recall@1, i.e. **retrieval is fine given correct routing; the weakness is the router.** Fix: sharper tool descriptions, an explicit disambiguation step, or a routing check before answering.

### Latency

End-to-end p50 ~28–33s, p95 76–157s — the demo's real weakness, dominated by Claude synthesizing long answers across two sequential calls (retrieval is ~300ms). Mitigation: stream the answer in the UI and/or cap answer length (Phase 8).

### Debugging done (in the plan's order)

1. **Eval too easy / metric too lenient** → added the strict section-path relevance bar and a per-category breakdown (didn't manufacture a curve — reported the truth).
2. **Hybrid bug** → found and fixed: the BM25 leg OR-ed *every* token including stopwords (`how/do/a/in`), matching nearly every chunk and polluting RRF; now ORs content words only. Improved hybrid retrieval, but vector still leads on this corpus.
3. **Rerank candidate pool** → already 30.

### Interview talking points

- **Lead with the per-category result, not the aggregate.** "On the *adversarial* questions, reranking doubled retrieval top-1 precision (40% → 80%). On *cross-product*, the BM25 hybrid leg won on recall. On *easy* questions, vector already saturates and the extras add noise. The aggregate hides this because it's dominated by easy questions — so I broke it down by query type."
- **The routing-robustness finding.** "I measured routing accuracy: 100% on normal single- and cross-product questions, but 20% on adversarial terminology lures — the agent follows the misleading word to the wrong product. That's a concrete, honest limitation; the retrieval is fine *given correct routing*, so the fix is in the router (tool descriptions / a disambiguation step), not the retrieval." Shows you find and reason about failure modes, not just happy paths.
- **rerank > hybrid on answer quality in every category**, even though pure vector is a strong baseline on this corpus — reranking is what makes the hybrid candidate set pay off.
- "The honest answer is *it depends on the corpus and query distribution*. I built the full hybrid + rerank pipeline **and** a rigorous eval that tells me exactly when each mode earns its keep."
- The eval **caught a real bug** (BM25 stopword pollution dragging hybrid below vector) that eyeballing the demo never would — that's the point of evals as quality gates.
- The methodology is the defensible part: two evaluation levels (retrieval + end-to-end), two relevance bars (lenient text / strict section), LLM-as-judge with a strict rubric over the Batches API, and a per-category cut. A flat or non-monotonic aggregate, honestly reported and decomposed, is more credible than a suspiciously clean curve.
- Where I'd push next: larger adversarial/cross samples (n=5/20 here), a harder/representative query set (exact IDs, abbreviations, typos), human-labeled gold chunks, and re-running at larger corpus scale where hybrid/rerank separate more.
