# Lessons Learned — Oracle Knowledge Navigator

A short engineering retrospective from building this project: a federated, agentic RAG system over three Oracle product knowledge bases (ERP, OCI, EPM), each exposed as an independent MCP server behind one orchestrating agent.

The throughline is **honesty and measurement**. Every lesson below was *earned* — from a bug, a wrong prediction, or a result that reversed under scrutiny — not assumed. Each is stated as a transferable principle, then grounded in what actually happened and the data behind it. Full methodology and per-run scorecards are in `TEST_LOG.md` and `DEVELOP_LOG.md`.

---

## On evaluation

**1. Measure on your own corpus and query distribution — don't ship the slogan.**
"Hybrid search beats pure vector" is received wisdom. On this corpus — clean, single-domain, with a strong embedder (`voyage-3-large`) — naive Reciprocal-Rank-Fusion hybrid was measurably *worse* than pure vector: equal-weight fusion drags the strong dense signal toward the weak lexical one. The honest verdict is regime-dependent — vector wins semantic queries, BM25 wins exact-token lookups, the reranker wins adversarial lures, and no single first-stage retriever wins everywhere. The lesson isn't "hybrid is bad"; it's that the answer depends on *your* data, and you should own the measurement that tells you when.

**2. A negative result is the start of an investigation, not the end.**
Rather than leave "hybrid lost" as a verdict, I asked *why*, and decomposed it into three measurable causes: (a) near-zero complementarity between the two retrievers — on this corpus the keyword leg *uniquely* rescued vector in only **2 of 75** queries, so fusion had almost nothing to add; (b) a single-target metric that rewards one chunk's rank, so fusion can only dilute a confident hit; (c) naive equal-weight fusion. Each was addressable. A per-query **adaptive** fusion (route semantic queries to dense, exact-token queries to lexical) turned hybrid from losing everywhere into *Pareto-dominating* vector (**+0.143** MRR on exact-term, zero regressions). And constructing the regime where dense genuinely fails — out-of-vocabulary identifiers the embedder can't represent — showed a reranked hybrid recovering what pure vector misses entirely (**recall 1.00 vs 0.27**). "It depends" is only a cop-out if you don't decompose it: hybrid's value is a function of **regime × metric × fusion mechanism**, and each axis is measurable.

**3. The metric can hide the truth — match it to what you actually care about.**
The *same* RRF hybrid that **lost** on a single-target metric (recall@1 / MRR against one relevant chunk) **beat both legs** on **set-recall** measured with pooled relevance judgments — and a reranked hybrid won outright (recall@10 **0.79**, nDCG **0.70**). Same system, opposite verdicts, purely from the choice of metric. For RAG specifically, set-recall is the right axis: the LLM answers from the *complete* retrieved set, not the single best chunk. Choosing the metric that matches the downstream task mattered more than any tuning.

**4. Distrust small samples — they lie in both directions.**
An early 5-question-per-category eval told two flattering stories at once: reranking *doubled* adversarial precision (40%→80%), and BM25 *tied* vector on exact-term lookups. Both felt too clean. At 15 per category, the "doubling" shrank to a modest edge and the "tie" flipped to a real win — the small sample had overstated one result and understated the opposite. A finding that survives a bigger sample (or honestly regresses) is worth more than an impressive number.

**5. Know exactly what your eval measures.**
After a chunking fix, the LLM judge's groundedness scores "crashed" to ~2.4. The fix was fine — the *judge* had gone blind: it was scoring a 200-character snippet that had covered ~35% of the old tiny chunks but only ~8% of the new larger ones. The answering model saw full chunks; only the judge was handicapped. Pointing the judge at full text restored groundedness to ~4.5. An eval-harness artifact can masquerade as a regression and send you chasing a problem that isn't there — always ask "what does the judge actually see?"

---

## On experimentation and judgment

**6. Run the cheap experiment even when you're certain.**
I had a clean argument for *skipping* an A/B: the reranker re-scores the candidate pool, so the pool's fusion method "can't" affect the final answer. I ran it anyway — and was wrong. The pool fusion *does* move the reranked result (cross-product MRR 0.475 → 0.516), because the reranker can only re-score what is *in* the pool, and different fusions select a different candidate set. That experiment decided the shipped default. "I predicted X, measured not-X, and here's the mechanism" is the most credible thing an engineer can say.

**7. A more sophisticated mechanism isn't worth shipping unless it strictly earns its keep.**
I built an IDF-based query router (weight the fusion by token rarity) expecting it to beat the simpler regex one. It fixed a real blind spot — rare acronyms — but introduced a confound: token *rarity* conflates a distinctive identifier with an off-domain "lure" word, so it hurt the adversarial regime. I kept the simpler, false-positive-free regex and did not ship the added complexity. Complexity has to pay for itself.

**8. Know which tool fits the problem — and name the ceiling instead of gaming the metric.**
I hardened adversarial routing with context engineering — encoding real product boundaries as *durable facts*, not question→answer mappings, so it generalizes rather than overfitting the test set. That fixed the clean lures, but on the strongest finance-vocabulary lures the router still *hedges* (queries the right product plus a wasteful extra). I could have tuned the wording to hit a perfect score, but that's gaming the metric; instead I documented the limit — prompt routing has a probabilistic ceiling, and a hard guarantee needs a routing classifier, not more words. Honesty about the residual is more credible than a suspiciously perfect number.

**9. Structural fixes beat asking the model to police itself.**
The worst wrong answer isn't a hallucination — it's a *confident, correctly-cited* answer built from the wrong source. A Planning question returned fluent, cited content drawn from a different EPM module (Financial Consolidation), because one collection held three module guides. Prompt-based fixes failed or only partly helped; what worked was structural — per-module search tools, each scoped so a Planning search *physically cannot* return Consolidation chunks. "Make it impossible" beats "ask it nicely" wherever reliability matters.

---

## On engineering discipline

**10. A cheap invariant catches silent quality bugs.**
A reconciliation check — chunks × tokens — surfaced ~213 tokens/chunk against a 400–800 target, exposing two chunker bugs (a dead `min_tokens` floor, and over-eager PDF heading detection that manufactured ~866 fake "headings" per document) that the working demo gave no hint of. The system *worked* the whole time; retrieval was just quietly worse than it should have been. Cheap invariants find what eyeballing outputs never will.

**11. Treat spend and time as real constraints — find a free proxy first.**
Before any run that costs real money (the full LLM-judge run is ~$30 and ~90 minutes), I de-risked in layers: chunk a downloaded PDF locally before embedding it; run a small slice before the full eval; isolate tuning levers on the *free* retrieval eval (no judge) before the expensive one; verify spend caps before every embed/judge. A fast local proxy almost always exists, and running it first turns an expensive gamble into a confirmation.

---

*Context: built as a personal project — a scaled-down but faithful, end-to-end build of the federation + RAG pattern, with retrieval quality measured rather than asserted. The reasoning behind every correction above is documented run-by-run in `TEST_LOG.md` and `DEVELOP_LOG.md`.*
