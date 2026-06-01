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

### Streaming (Phase 8 polish)

Added a streaming path so the demo doesn't sit dead for ~30s while Claude synthesizes: the orchestrator exposes `POST /query/stream` (Server-Sent Events) alongside the unchanged `POST /query` (the eval's JSON contract). Events are `tool_call` (trace builds live), `answer_delta` (answer streams token-by-token via `messages.stream()`), and `done` (final trace + latency). The UI re-renders the trace as tool calls arrive and streams answer text into a placeholder.

- **Verified (VPS, CLI):** `python -m orchestrator.agent --stream` prints answer tokens incrementally as they arrive — confirms the Anthropic stream → generator → SSE path end to end on the orchestrator side.
- **Verified (VPS, browser):** loading the live demo and asking a question streams the answer in token-by-token — confirms SSE survives Traefik (no proxy buffering) and Streamlit re-renders live. `X-Accel-Buffering: no` + `Cache-Control: no-cache` are set on the response to discourage proxy buffering.

### Cost dashboard (Phase 9 stretch goal)

The orchestrator now accumulates Claude token usage across every step of the tool-use loop and computes a per-question cost (`claude-sonnet-4-6` list price: $3/$15 base per Mtok, $3.75 cache-write 5m, $0.30 cache-read). It returns/emits a `cost` object on `query()`, the streaming `done` event, and the CLI; the UI renders a **cost panel below the trace** (USD, output tokens, "input served from cache" %).

- **Honest scope:** Claude spend only — the Voyage embed/rerank cost (inside the MCP servers' `retrieve()`) is **not** counted, and is labelled as such everywhere. Claude dominates per-question cost, so this is the defensible headline, not a full bill.
- **Sanity figures (arithmetic):** single-product ~$0.01–0.017, cross-product ~$0.03. The "input served from cache" metric reads ~0% on a cold first query and ~90%+ once the system+tools prefix is cached — a live, visible demonstration of the prompt-caching work.
- **Interview talking point:** "I instrumented per-question cost — token breakdown and dollar figure in the trace. The cache-hit metric makes the prompt-caching savings visible: the first question pays to write the cache, every one after reads it for ~10% of the price."
- **Verified (VPS, browser):** the cost panel renders live below the trace on the demo, with the per-token caption and the "input served from cache" metric. Arithmetic verified separately.

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

Routing accuracy by category: single **100%**, cross **100%**, adversarial **20%**. (Adversarial routing was then hardened to **100%** — see "Routing hardening" below.)

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

- **Adversarial questions → rerank wins, recall@1 40% → 80%.** When terminology is misleading (e.g. "reverse a *consolidation* journal" lures ERP but the answer is EPM), rerank's semantic precision surfaces the right section. This is exactly what a reranker is for. **[Superseded 2026-05-31 — small-sample artifact. n was only 5 here. After rebalancing adversarial to n=15, rerank's edge shrank to recall@1 53% vs 40% (MRR 0.585 vs 0.539) — a modest advantage, not a doubling. See "Dataset rebalance + refreshed scorecard" at the end.]**
- **Cross-product questions → hybrid wins** (MRR 0.757 > vector 0.714): the BM25 keyword leg catches the specific cross-domain sections; rerank over-reorders on the mixed-domain query and dips.
- **Easy single-product → vector ≈ rerank > hybrid**: voyage-3-large already retrieves the right section first ~87% of the time; hybrid's keyword candidates add RRF noise; rerank cleans it back to vector's level.

So both DoD criteria hold *in the right regime* — hybrid beats vector on recall for cross-product, and **rerank beats hybrid on judged answer quality in every category** (overall 3.45 vs 3.14; single 3.71 vs 3.31). Hybrid + rerank earn their keep most on **harder inputs**; on a clean, well-embedded corpus with well-formed questions, pure vector is a strong baseline. (Caveats: adversarial n=5, cross n=20 — modest samples; directional.)

### The routing finding (end-to-end)

Routing accuracy is **100% on single-product and cross-product** questions but **20% on adversarial** ones: the orchestrator is **fooled by terminology lures** — "reverse a *consolidation* journal" routes to ERP (the word "journal") instead of EPM; "compartments for *financial data*" routes to ERP instead of OCI IAM. This is the cause of the low *end-to-end* adversarial quality across all three modes — it's a **routing miss, not a retrieval miss**. The retrieval scorecard (which forces the correct product) shows rerank doubling adversarial recall@1, i.e. **retrieval is fine given correct routing; the weakness is the router.** Fix: sharper tool descriptions, an explicit disambiguation step, or a routing check before answering.

### Routing hardening (the fix)

I took the obvious fix — **context engineering, not code**. Three changes (Phase 8): (1) a "route by the distinctive concept, not generic vocabulary" principle in the orchestrator system prompt, naming the weak signals (*journal*, *allocation*, *translate*, *financial*, *security*, *users*, *policy*) and the qualifiers that actually decide; (2) each MCP server's scope description now *claims* its discriminating concepts and *names the adjacent-product boundary* — e.g. EPM explicitly owns "consolidation journals", "allocation rules in Planning", and "currency translation to a parent currency during the close", while ERP's note says those belong to EPM despite the shared words; (3) OCI claims "compartments organizing resources including financial data" and "policies controlling which users access which resources", with a boundary note that access control is OCI even when the data is financial.

**The honesty guardrail:** I encoded *durable Oracle product-boundary facts* (true regardless of my test set), **not** question→answer mappings. I deliberately did not write "if asked about a consolidation journal, route to EPM." That line is what keeps the re-measured number a real generalization rather than overfitting to the 5 eval questions.

**Result — end-to-end routing accuracy on the 5 adversarial questions (re-run, `EVAL_CATEGORY=adversarial`):**

| | adversarial routing acc |
|---|---|
| before hardening | **20%** (1/5) |
| after hardening | **100%** (5/5) |

The 30 single + 10 cross questions still routed correctly in the same run cycle (no regression). The **retrieval scorecard is unchanged by this fix** (vector 40% / hybrid 40% / rerank **80%** recall@1, strict bar) — and that's the right sanity check: the scope edits live in MCP *tool descriptions* that only the router reads, while `retrieve()` is untouched, so retrieval metrics *must* be invariant. The fix moved routing without touching retrieval.

Caveat: n=5, so "100%" means 5/5 — directional, not a precise rate. The lift is real (the lures that previously fooled the router now route correctly) but the sample is small.

**End-to-end *quality* after the fix (same adversarial-only re-run, LLM-judged):** vector 2.87 / hybrid 2.80 / rerank **2.93** (1–5), at 100% routing. Compared to the pre-hardening table above (2.87 / 2.60 / 2.80 at 20% routing), judged quality stayed in the same ~2.6–2.9 band — it did **not** jump. That's the expected, honest result: the fix corrected *which product* the agent queried without changing retrieval or synthesis, and on these particular 5 questions the judge scored the answers similarly either way (n=5, so quality deltas here are noise). The clean, defensible win is the routing metric (20% → 100%), not a quality bump.

**Operational bug surfaced during this re-run — since fixed:** aborting a run mid-request (Ctrl-C during an in-flight tool call) left one of the orchestrator's persistent MCP sessions in a broken state, and subsequent queries returned HTTP 500 until `docker compose restart orchestrator`. Root cause: `connect()` opened the MCP sessions in the *startup* task but they were shared across all *request* tasks, so one aborted request corrupted a session a later request reused.

**The fix (Phase 8):** the orchestrator now uses **per-request MCP sessions**. `connect()` discovers each server's tools once (read-only state: tool defs + system prompt) and closes those sessions immediately; `query()` opens fresh sessions inside an `AsyncExitStack` — lazily, one per product as the agent first routes to it — and closes them all when the request ends, including on error or cancellation. Nothing is shared between requests, so an aborted request can't poison a later one. Cost: a fresh `initialize()` handshake per product per request (~tens of ms over the internal Docker network), negligible against ~30s query latency.

**Verified by reproducing the original trigger:** (1) a normal query returned a full cited answer; (2) a run was deliberately Ctrl-C'd mid-request; (3) an immediate re-run completed all 15 queries with **zero 500s and no restart** — exactly the sequence that previously required `docker compose restart orchestrator`. The `DELETE …/mcp 200 OK` lines in the orchestrator log confirm sessions are now torn down cleanly per request.

### Latency

End-to-end p50 ~28–33s, p95 76–157s — the demo's real weakness, dominated by Claude synthesizing long answers across two sequential calls (retrieval is ~300ms). Mitigation: stream the answer in the UI and/or cap answer length (Phase 8).

### Debugging done (in the plan's order)

1. **Eval too easy / metric too lenient** → added the strict section-path relevance bar and a per-category breakdown (didn't manufacture a curve — reported the truth).
2. **Hybrid bug** → found and fixed: the BM25 leg OR-ed *every* token including stopwords (`how/do/a/in`), matching nearly every chunk and polluting RRF; now ORs content words only. Improved hybrid retrieval, but vector still leads on this corpus.
3. **Rerank candidate pool** → already 30.

### Interview talking points

- **Lead with the per-category result, not the aggregate — and with the sample-size correction.** "On *adversarial* questions, an n=5 pilot showed reranking *doubling* top-1 precision (40%→80%) — but I distrusted n=5, expanded adversarial to 15, and the gap regressed to a *modest* edge (53% vs 40%). On *cross-product*, vector actually beats rerank. On *easy* questions, vector already saturates. So the honest takeaway is that pure vector is the strongest mode on this corpus and rerank's wins are smaller than the pilot suggested — which I'd never have caught without rebalancing the dataset." (This *is* the strong story: a self-corrected finding beats an impressive-but-fragile one.)
- **The routing-robustness finding *and the fix*.** "I measured routing accuracy: 100% on normal single- and cross-product questions, but 20% on adversarial terminology lures — the agent follows the misleading word to the wrong product. The retrieval is fine *given correct routing*, so the fix was in the router, not the retrieval: I sharpened the tool/scope descriptions to encode the real Oracle product boundaries — that took adversarial routing from 20% to 100%, with retrieval metrics unchanged (the scope edits only affect what the router reads, not `retrieve()`)." This is the strongest single story — **measured a failure mode, diagnosed it precisely, fixed it with context engineering, re-measured, and the fix was provably isolated to the layer it should touch.** And the honesty note: I encoded durable product-boundary facts, not question→answer mappings, so it generalizes rather than overfits the 5 eval questions.
- **rerank > hybrid on answer quality in every category**, even though pure vector is a strong baseline on this corpus — reranking is what makes the hybrid candidate set pay off.
- "The honest answer is *it depends on the corpus and query distribution*. I built the full hybrid + rerank pipeline **and** a rigorous eval that tells me exactly when each mode earns its keep."
- The eval **caught a real bug** (BM25 stopword pollution dragging hybrid below vector) that eyeballing the demo never would — that's the point of evals as quality gates.
- The methodology is the defensible part: two evaluation levels (retrieval + end-to-end), two relevance bars (lenient text / strict section), LLM-as-judge with a strict rubric over the Batches API, and a per-category cut. A flat or non-monotonic aggregate, honestly reported and decomposed, is more credible than a suspiciously clean curve.
- Where I'd push next: larger adversarial/cross samples (n=5/20 here), a harder/representative query set (exact IDs, abbreviations, typos), human-labeled gold chunks, and re-running at larger corpus scale where hybrid/rerank separate more.

---

## Post-chunking-fix re-eval & grounding investigation (2026-05-31)

A chunking-quality fix (corpus re-packed 7740 → 2574 right-sized chunks; see `DEVELOP_LOG.md`) prompted a full re-eval. The fix moved the numbers in three distinct ways, and chasing the third surfaced a real, pre-existing grounding weakness. New scorecards: `20260531T022451Z_retrieval` (post-fix baseline), `20260531T024257Z_summary` (judge), `20260531T123618Z_retrieval` (floor=0.6).

### Three-layer result

1. **TEXT-bar retrieval held; SECTION bar regressed.** Lenient recall stayed ~98% — content is still retrievable. Strict section-path recall fell (`hybrid_rerank` MRR 0.78 → 0.61) because merged chunks carry the *longest shared* heading path, so breadcrumbs are shallower. An offline experiment proved this is **not tunable away**: four labeling rules (common-prefix / first-block / deepest / dominant) moved path-keyword recoverability only 78% → 80%, and reverting the heading detection only 80% → 84%. The old over-fragmentation was *accidentally* optimal for a path-keyword metric. Accepted tradeoff.

2. **The judge "quality drop" was largely an eval artifact.** Correctness/groundedness looked down vs the 05-29 baseline, but (a) that baseline pre-dates Phase 8/9 changes — not a clean A/B; (b) decisively, the judge sees only a **200-char snippet** per chunk (`agent.py` `_summarize_result`, trace-only) — ~35% of an old tiny chunk but only ~8% of a new ~660-tok one — so groundedness was systematically under-measured. The *answering* model gets full chunk text, so answers weren't degraded by chunk size. **Fixed 2026-05-31** — the judge now scores full chunk text (commits `60c4b49`/`5321228`); a 6-question slice showed groundedness recover **~2.4 → ~4.5**, and `epm-plan-scenario` scored **5.0/5.0** correctness/groundedness end-to-end once the coverage guide was ingested.

3. **Hallucination on weak retrieval — real and pre-existing.** Lowest-groundedness rows were genuine retrieval misses: off-topic/boilerplate chunks returned, then the agent answered from general knowledge. Root cause: **no relevance floor**, so the system prompt's "abstain if nothing relevant" never fired (`retrieve()` always returned top_k).

### Fixes attempted (and their honest verdicts)

- **Relevance floor (fix #1) — SHELVED.** `RETRIEVAL_MIN_RERANK_SCORE`, rerank-only (the one calibrated-score mode; RRF and cosine aren't). VPS A/B: at **0.6** it cleanly abstains on single-product coverage-gap queries (off-topic ~0.50–0.55) while keeping good hits (~0.71–0.84). **But** it craters **cross-product** recall **90% → 25%**: legitimate cross-product chunks also score ~0.5 (each only partly addresses a compound query), overlapping the off-topic band — *no global threshold separates them*. Left inactive by default; mechanism retained for single-product use.
- **Grounding prompt #3 — insufficient.** "Abstain when passages don't address the question" made the model search harder and cite sources (good traceability) but it still synthesized FCC content into a Planning answer.
- **Sub-domain-aware prompt — partial win.** Naming the exact trap (EPM = Planning/FCC/Narrative, don't cross-apply; use each chunk's source doc; honor in-text applicability notes) got the answer to **lead with Planning-genuine content, label FCC-sourced quotes, and cite the Planning guide as the authority** — but it still blends some FCC material.

### Root cause & the honesty note

The `epm` knowledge base **conflates three EPM modules in one collection**, so a Planning question retrieves FCC chunks. The durable fix is data/architecture (module scoping), not prompt-tuning — **now implemented and verified** (see "Cross-module bleed fix" below).

**On the limits of automated verification:** a fact-check agent labeled the FCC-sourced answer "well-cited-but-wrong" because the FCC guide marks those scenario properties "not used in Financial Consolidation and Close." That verdict **over-read the disclaimer** — Start/End Yr and Exchange Rate Table are almost certainly genuine *Planning* scenario properties (the FCC note means "FCC ignores these," implying they apply elsewhere). At this depth both the system's answers *and* our automated checks carry Oracle domain uncertainty; reliable adjudication needs an SME. We stopped tuning here.

### Interview talking points (this arc)

- **A fix that "passes" its target metric can still move others — measure the blast radius.** The chunking fix nailed chunk size and held TEXT-bar recall (98%), but I checked the strict SECTION bar too and found a real regression, then *proved* it wasn't tunable (4 labeling rules + a heading-detection revert) rather than guessing.
- **Distinguish a real regression from an eval artifact.** The judge's groundedness "drop" was mostly a 200-char trace-snippet truncation interacting with bigger chunks — the answering model had full context. Knowing *what the judge actually sees* mattered more than the number.
- **Calibrate thresholds to the right score scale.** My first floor (0.1) was inert because I'd reasoned from RRF scores (~0.03); the production reranker scores off-topic at ~0.5. Re-derived from data (good ~0.8 vs no-answer ~0.5) → 0.6.
- **Know when a knob can't work.** A single relevance floor can't separate cross-product-relevant (~0.5) from off-topic (~0.5) — they overlap. Recognizing the impossibility beat shipping a number that quietly halves cross-product recall.
- **Name the failure mode in the prompt.** Generic "don't guess" failed; "EPM = three modules that don't cross-apply, here's the source signal" partly worked. Specific beats general.
- **Be honest about your own tools.** The fact-check agent was confidently wrong (over-read a disclaimer). Automated verification is a strong filter, not an oracle — at domain-expertise depth, flag the uncertainty instead of trusting the verdict.

### Cross-module bleed fix — module-scoped EPM retrieval (2026-05-31)

The EPM cross-module bleed (a Planning question synthesizing FCC content) is fixed with **routing-based module scoping**, chosen over a model-set filter param or query keyword auto-detection because it makes the bleed *structurally impossible* given correct routing and reuses the tool-selection mechanism already at 100% on cross-product routing. **No re-ingest** — `doc_id` was already stored.

- **Data layer:** `qdrant_store.search` + `db.search_bm25` take a `doc_ids` filter, threaded through `retrieve()` (commit `e26bf0f`, smoke-tested).
- **Tools:** the EPM server exposes `search_planning` / `search_fcc` / `search_narrative` (each scoped by `doc_id`) instead of one `search_docs`; routing to a module's tool can't return another module's chunks (`e36256a`). ERP/OCI unchanged.
- **Prompt:** route to the named module's tool; don't call a sibling to fill a gap (`b3af7a7`).

**Live verification (VPS), both directions:**

| question | tools called | sources | outcome |
|---|---|---|---|
| "manage scenarios and versions in EPM **Planning**" | `epm_search_planning` ×5 | Planning guide only | **no FCC bleed**; honestly notes dimension-editor CRUD is in a non-ingested companion guide |
| "post a **consolidation journal** during the close" | `epm_search_fcc` ×4 | FCC guide (Ch. 19) | correctly FCC-grounded |

**The progression that makes the talking point:** same Planning question, three attempts — (1) no fix → confident FCC answer; (2) prompt-only → still blended FCC ("well-cited-but-wrong"); (3) module-scoped tools → `search_planning` only, FCC structurally impossible. **A structural fix beat soft prompt guidance** — the layer the fix lives in matters more than how strongly you word the instruction. And the fix turned a *masked* failure (FCC-as-Planning) into an *honest* one (the answer now says which content isn't in its sources and where to look).

## Dataset rebalance + refreshed retrieval scorecard (2026-05-31)

The original 30/10/5 split over-weighted easy single-product questions (which saturate) and under-sampled the discriminating categories — adversarial carried its headline finding on **n=5**. Rebalanced to **15/15/15** (commit `a0012e3`): single trimmed to 5/product; cross +5 (erp↔epm); adversarial +10 lures across all three target products, including *inverted* lures (e.g. "allocate overhead *in the general ledger*" → ERP, mirroring the existing "allocation in Planning" → EPM) so the router can't memorize a keyword→product shortcut. Every new question's keywords verified present in the target corpus. Scorecard `20260531T152430Z_retrieval`.

**Refreshed retrieval scorecard — strict section bar, recall@1 / MRR (the n=5 → n=15 correction):**

| category (n) | vector_only | hybrid | hybrid_rerank |
|---|---|---|---|
| single (15) | 67% / 0.744 | 60% / 0.706 | 67% / **0.747** |
| cross (30) | **43% / 0.569** | 27% / 0.475 | 33% / 0.449 |
| adversarial (15) | 40% / 0.539 | 33% / 0.506 | **53% / 0.585** |
| **overall (60)** | **48% / 0.605** | 37% / 0.541 | 47% / 0.558 |

(TEXT/lenient bar still saturates: ~98% all modes.)

**What changed vs the n=5 story:**
- **The "rerank doubles adversarial recall@1 (40→80%)" finding did not survive.** At n=15 it's recall@1 **53% vs 40%**, MRR **0.585 vs 0.539** — rerank is still the best mode on adversarial, but the advantage is *modest*, not a doubling. The n=5 80% was a lucky sample.
- **Cross-product: vector_only beats rerank — now robust at n=30** (MRR 0.569 vs 0.449; recall@1 43% vs 33%). This finding *strengthened*.
- **Single: tie** (vector ≈ rerank, both ~0.745).
- **Overall, `vector_only` is the strongest mode on this corpus** (MRR 0.605) — it wins cross, ties single, trails only modestly on adversarial.

**Honest refreshed takeaway:** on this clean, well-embedded corpus with these query types, **`vector_only` was the strongest retrieval mode** *as configured*; hybrid's RRF leg adds noise, and the **untuned** reranker earned only a modest edge on adversarial while hurting cross-product. The earlier "rerank decisively wins adversarial" was a small-sample overstatement — caught by expanding the thin category. **(Update: the reranker was then tuned to ~parity — see "Tuning hybrid_rerank" below — after which it wins adversarial and ties overall.)** (Caveat: `retrieval_eval` forces correct product, so this is retrieval precision *given* routing; the end-to-end routing-robustness re-run on the new lures is still deferred behind the spend cap.)

### Tuning hybrid_rerank to ~parity (2026-05-31)

The refreshed scorecard put `vector_only` ahead of `hybrid_rerank` on the strict bar. Two env-toggleable levers (`81eb3fe`), A/B'd via the free `retrieval_eval`:
- `RERANK_POOL=vector` — rerank the vector top-N instead of the RRF/hybrid pool.
- `RERANK_INCLUDE_PATH=1` — prepend each chunk's `section_path` to the text rerank-2 scores.

**Attribution (strict-bar overall MRR; scorecards `181717` / `181929` / `153941`):**

| config | overall | cross | adversarial |
|---|---|---|---|
| baseline (RRF pool, no path) | 0.558 | 0.449 | 0.585 |
| vector pool **alone** | 0.558 | 0.464 | 0.558 |
| section-path **alone** | 0.572 | 0.476 | 0.591 |
| **both (now default)** | **0.598** | **0.515** | **0.617** |

**The levers are synergistic — super-additive.** Alone they give +0.000 and +0.014; together +0.040. Mechanism: section-path is a *discriminating* signal that gets diluted by junk on the noisy RRF pool but separates cleanly on the vector pool — clean pool + structural context, each necessary, neither sufficient. My initial "RRF noise is the weak link" hypothesis was **wrong** (vector pool alone did nothing); the honest driver is the *interaction*, found only by isolating the levers.

**Result:** tuned `hybrid_rerank` now **wins adversarial** (0.617 vs vector 0.539), **ties single** (0.747), **halves the cross gap** (0.449→0.515 vs 0.569), and is **~level overall** (0.598 vs 0.605). TEXT bar unchanged (~0.975), so no recall cost. Both levers are the default (`a528e14`, env-overridable) and deployed to the live demo. Scorecard `20260531T153941Z`.

**Interview point:** I didn't accept "vector wins, rerank underperforms" — I formed two hypotheses, made them env-toggleable, A/B'd them for free on the retrieval eval, and *isolated* them. The pretty hypothesis (RRF noise) was wrong; the real win was a non-obvious synergy between a clean pool and structural context. Reranker now ~parity overall and best-in-class on the hard adversarial queries.

### Component ablation — adding the keyword_only (BM25) baseline (2026-05-31)

Added a `keyword_only` mode (BM25/FTS5 only; commit `74c0d2f`) so the full component ablation is *measured*, not inferred — keyword/vector/hybrid/hybrid_rerank. Scorecard below (strict section bar, overall MRR, n=60):

| mode | overall MRR | role |
|---|---|---|
| keyword_only (BM25) | **0.485** | lexical component — weakest |
| hybrid (RRF fusion) | 0.541 | fuses the two |
| hybrid_rerank (tuned) | 0.598 | + reranking |
| vector_only (dense) | **0.605** | semantic component — strongest |

**What it settles:** the two components are keyword **0.485** and vector **0.605**; naive equal-weight RRF (`hybrid`) lands at **0.541** — *between* them, *below* the stronger. So on this corpus fusion helps the weak side and **hurts the strong side** — the dilution effect, now measured. "Is hybrid better than each alone?" → better than keyword (0.541>0.485), worse than vector (0.541<0.605).

**Secondary findings:**
- **Lenient bar exposes BM25's nature:** `keyword_only` hits 100% recall@5/@10 on the TEXT bar (BM25 retrieves keyword-containing chunks *by construction*) yet is weakest on the strict bar — it finds the right *words*, not the right *section*. This is why the lenient bar saturates and the strict bar discriminates.
- **Adversarial: keyword is worst (0.436)** — terminology lures are precisely BM25's failure mode, which is why those queries need semantics + reranking (rerank 0.617).

`keyword_only` is an analysis/ablation mode only — not in the end-to-end runner or the MCP/demo surface (production stays on hybrid_rerank).

### Exact-term slice — does BM25 beat dense on lexical lookups? (2026-05-31)

The query set was semantic-biased, so the ablation under-tested the lexical regime BM25 is built for. Added 5 exact-term questions (tag `exact_term`: `OEP_Working`, `XCC` flexfield, `DRG`, `NSG`, `Create Accounting` — exact tokens grounded in chunk *text*, some rare at 2–3 chunks) and a tag-based **TEXT-bar** slice (the right metric, since these tokens live in body, not headings). Single stays 15 = 10 semantic + 5 exact (`8613eaa`). Scorecard `20260531T213348Z`.

**Result (exact-term slice, TEXT bar, n=5):**

| mode | recall@1 | MRR |
|---|---|---|
| keyword_only | 80% | 0.867 |
| vector_only | 80% | 0.867 |
| hybrid | 80% | 0.825 |
| hybrid_rerank | **100%** | **1.000** |

**A clean negative result.** BM25's textbook advantage on exact terms **did not appear** — `keyword_only` exactly *ties* `vector_only` (both 0.867). voyage-3-large embeds even rare exact tokens well enough to keep pace, so the lexical leg adds no edge on this corpus. `hybrid` (RRF) is *slightly worse* even here (0.825) — fusion dilution in every regime. The tuned `hybrid_rerank` wins the slice outright (1.000): the complementarity surfaces through *reranking the candidate pool*, not RRF fusion.

**Cross-regime conclusion:** `vector_only` ≈ tuned `hybrid_rerank` are strongest across semantic, adversarial, *and* exact-term queries; naive `hybrid` underperforms everywhere; `keyword_only` is weakest except on exact-term, where it only ties. **Interview point:** I hypothesised exact-term queries would expose a BM25 edge and justify hybrid; I tested it and got a null — a strong modern embedder erases the classic lexical advantage on this corpus. Honest negative results are part of a credible eval. (n=5 — directional.)

### Exact-term, n=15 — BM25's edge appears, and the all-regime verdict (2026-05-31)

Promoted `exact_term` to a full category (15 questions; dataset now 60 = 15/15/15/15) and re-ran. The earlier **n=5** exact-term slice showed `keyword_only` *tying* `vector_only` (a null). At **n=15** that reverses — the BM25 edge appears (scorecard `20260531T214502Z`, TEXT bar):

| mode | recall@1 | MRR |
|---|---|---|
| hybrid_rerank | 100% | 1.000 |
| keyword_only | 93% | 0.956 |
| hybrid | 93% | 0.942 |
| vector_only | 87% | 0.906 |

**`keyword_only` (0.956) now beats `vector_only` (0.906) on exact-term**, as does `hybrid` (0.942) — vector is the *worst* first-stage mode here, missing exact tokens BM25 nails. The n=5 tie was small-sample noise; the lexical-complementarity signal is real on a credible sample. **Supersedes the "clean null" recorded just above.**

**Methodological symmetry (the interview point):** n=5 *overstated* rerank on adversarial (doubling → modest) **and** *understated* keyword on exact-term (tie → win). Both directions show n=5 is unreliable — which is exactly why the 15-per-category rebalance mattered.

**All-regime verdict — no single first-stage mode dominates:**

| regime | best first-stage | worst |
|---|---|---|
| semantic (single/cross) | vector | keyword |
| exact-term (lexical) | keyword | vector |
| adversarial | (rerank) | keyword |

`hybrid_rerank` is the **only mode that wins or ties every regime** (best on adversarial 0.617 and exact-term 1.000; ~ties vector on single 0.747; competitive on cross 0.515). That is the empirical justification for shipping it: when the query distribution is unknown, the reranker is the robust choice — vector covers semantic, keyword covers lexical, rerank covers both.

### Refreshed end-to-end LLM-judge run — 60 questions, current system (2026-06-01)

First full end-to-end run on the *current* stack (60-q dataset, tuned rerank default, module-scoped EPM tools, coverage fix, judge-snippet fix). 60 q × 3 modes, LLM-judged. Summary `20260531T221433Z`.

**`hybrid_rerank` wins judged answer quality in every category** (overall 3.97 vs vector 3.79 vs hybrid 3.66):

| category | vector | hybrid | hybrid_rerank |
|---|---|---|---|
| single | 4.27 | 4.29 | **4.36** |
| cross | 3.02 | 2.76 | **3.27** |
| adversarial | 4.13 | 3.84 | **4.22** |
| exact_term | 3.76 | 3.76 | **4.04** |

On *answer quality* (what ships) the tuned reranker is best across the board — a stronger result than the retrieval-level story, and the empirical case for the production default. **Groundedness recovered to ~3.7–4.1** (rerank 4.10) — confirms the judge-snippet fix end-to-end (it was an artifact-depressed ~2.4 before).

**Routing — 100% on single/cross/exact_term; adversarial ~80%, but it's *hedging*, not misrouting.** Every adversarial "miss" is the correct product **plus** one extra lure-suggested product — the router never routes to *only* the wrong place:

| lure | expected | routed | pull |
|---|---|---|---|
| archive **financial statements**… | oci | +epm/erp | "financial" |
| isolate department **budget** data… | oci | +epm | "budget" |
| balance **intercompany**… two ledgers | erp | +epm | "intercompany" |
| **security policy**… which users access… | oci | +erp | "security" |

The answers stayed correct (adversarial groundedness 4.3–4.5). Some lures are *genuinely* cross-product (intercompany spans ERP+EPM; financial data in OCI storage touches both), so hedging is partly correct behavior; the strict set-match metric just penalizes the extra call. (Per-mode routing spread 87/73/80% is LLM nondeterminism — routing is mode-independent.) Tightening these 4 scope descriptions (Phase-8 style) could trim the hedge, at the risk of over-constraining the genuinely-ambiguous ones.

### Routing hardening for the cloud-infra lures — partial, and the honest ceiling (2026-06-01)

The end-to-end run (above) showed the router *hedging* on adversarial lures that wrap a cloud-infra question in finance vocabulary. Hardened the scope boundaries (commit `2c370d6`): OCI now explicitly *claims* storing/archiving any files (incl. financial statements) and isolating budget/financial data into compartments; ERP and EPM *disclaim* cloud storage/archival/compartment isolation — durable product-boundary facts, not question→answer mappings. Left the genuinely cross-product `intercompany` lure alone.

**Spot-checks (single runs):** all three hardened lures routed cleanly to **OCI only** — `budget data into its own space`, `archive financial statements`, `security policy / which users access`.

**But the adversarial re-run (`EVAL_CATEGORY=adversarial`, summary `20260601T002238Z`) showed routing stayed ~80%** (vector 80 / hybrid 87 / rerank 80) — *not* the ~93% the spot-checks suggested. Pulling the JSONL:
- **`security-policy-users` — fixed** (gone from the miss list).
- **`archive-financial` (→ERP) and `compartment-budget` (→EPM) still hedge ~half the time** — the single spot-checks just got lucky; across more samples the strong words ("financial statements", "budget") still pull the extra product intermittently.
- **`gl-intercompany` hedges** — expected; intentionally left (genuinely spans ERP+EPM).

**The honest lesson:** prompt-based routing has a **probabilistic ceiling** — hardening shifts the odds (fixed one lure, reduced the others) but can't make a soft prompt deterministic, and over-fitting the wording to chase the metric is what the eval exists to prevent. Crucially the residual is **"right product + a wasteful extra," never wrong-routing**, so it's harmless: this run scored **quality 4.04–4.18, groundedness 4.27–4.40, recall 93–100%** on adversarial — the answers are correct; the strict set-match metric just penalizes the extra call. A *hard* guarantee would need a structural fix (a routing classifier or a post-route relevance check before answering) — logged as future work, out of scope for the demo.
