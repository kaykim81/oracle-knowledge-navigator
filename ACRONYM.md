# Glossary — Acronyms & Domain Terms

A reference for the difficult or domain-specific terms used in `DEVELOP_LOG.md` (and the
rest of the project docs). Grouped by area. Kept local (gitignored) like the other
handover docs.

---

## Oracle products & the three corpora

| Term | Meaning |
|---|---|
| **ERP** | *Enterprise Resource Planning* — software that runs core business operations (finance, procurement, etc.). Here it specifically means **Oracle Fusion Cloud ERP (Financials)** — the product whose docs form the `erp` corpus. ERP **records** transactions. |
| **EPM** | *Enterprise Performance Management* — software for planning, budgeting, consolidation, and financial close. Here, **Oracle Fusion Cloud EPM**, forming the `epm` corpus. EPM **consolidates/plans on top of** the data ERP records — this ERP↔EPM boundary is what the router must respect. |
| **OCI** | *Oracle Cloud Infrastructure* — Oracle's IaaS/cloud platform (compute, networking, storage, identity). Forms the `oci` corpus. |
| **Fusion / Fusion Cloud** | Oracle's brand for its modern SaaS applications suite (ERP, EPM, HCM, etc.). |
| **Fusion Financials** | The financials modules of Fusion Cloud ERP (General Ledger, Payables, Receivables, Assets). |
| **GL** | *General Ledger* — the central accounting record of a company; one of the ERP source guides (`erp-general-ledger`). |
| **25D** | An Oracle release label (year 20**25**, quarterly update **D**). The ERP guides are from the 25D release. |
| **BYOIP** | *Bring Your Own IP* — an OCI networking feature letting customers import their own public IP address ranges. Used as a sample OCI query. |
| **IAM** | *Identity and Access Management* — the OCI service for users, groups, and permissions; one of the four crawled OCI services. |
| **corpus / corpora** | A structured collection of documents used as the searchable knowledge base. *Corpora* is the plural. This project has three: `erp`, `epm`, `oci`. |

---

## Oracle domain terms & eval-probe identifiers

Surfaced as the eval grew **exact-term** and **OOV** slices — specific tokens used to probe lexical-vs-semantic retrieval. Grouped by product.

**EPM (modules & Planning members):**

| Term | Meaning |
|---|---|
| **Planning / FCC / Narrative** | The three EPM modules sharing the `epm` corpus — **Planning** (budgeting/forecasting), **FCC** = *Financial Consolidation and Close*, **Narrative** = Narrative Reporting. The EPM server exposes a per-module search tool for each, so a Planning query can't surface FCC chunks. |
| **OEP_\*** | Prefix for EPM Planning **scenario / version members** — `OEP_Working`, `OEP_Plan`, `OEP_Forecast`, `OEP_Target`, `OEP_Original`, etc. Near-identical sibling names — the confusable-identifier ("regime A") case where a vector search grabs the wrong sibling. |
| **flexfield / XCC** | A *flexfield* is an Oracle-configurable data field. **XCC** is a flexfield code in Budgetary Control integration — a rare (df=2) token, used as an OOV probe. |
| **OFS_ / OWP_** | EPM member prefixes — **OFS** = Oracle Financial Statement (planning), **OWP** = Oracle Workforce Planning (e.g. `OFS_Rollup`, `OWP_Salary`). Ultra-rare (df=2) OOV probes. |

**ERP / Fusion Financials (database tables):**

| Term | Meaning |
|---|---|
| **AP_ / RA_ tables** | Oracle Financials table names — **AP_** = Accounts **P**ayable (`AP_INVOICE_LINES_ALL` vs the header table `AP_INVOICES_ALL`), **RA_** = **R**eceiv**A**bles (`RA_CUSTOMER_TRX_ALL`, `RA_INTERFACE_LINES_ALL`). Header-vs-lines sibling pairs — confusable identifiers a user might search verbatim (the "function name in a codebase" analog). |
| **GL_INTERFACE** | The General Ledger interface table journals are imported through; `REFERENCE1` and `BUDGET_VERSION_ID` are columns in it (OOV probes). |
| **AutoInvoice** | The Receivables import process that stages invoice data in `RA_INTERFACE_LINES_ALL`. |

**OCI (networking & compute):**

| Term | Meaning |
|---|---|
| **VCN** | *Virtual Cloud Network* — OCI's software-defined private network (the single most common OCI term). |
| **DRG** | *Dynamic Routing Gateway* — connects a VCN to on-prem or other networks. |
| **NSG** | *Network Security Group* — a firewall-rule set attached to VNICs (vs subnet-level *security lists*). |
| **VNIC** | *Virtual Network Interface Card* — a network interface attached to a compute instance. |
| **OCID** | *Oracle Cloud Identifier* — the unique ID OCI assigns every resource. |
| **NAT (gateway)** | *Network Address Translation* — lets private instances reach the internet outbound-only. |
| **CIDR** | *Classless Inter-Domain Routing* — the IP-range notation (e.g. `10.0.0.0/16`) for VCNs/subnets. |
| **shape / Flex shape** | An OCI compute size. **`VM.Standard.E4.Flex`** = VM, Standard series, 4th-gen AMD (**E4**), flexible OCPU/RAM. Sibling shapes (E3/E5/E6) make these confusable identifiers. |
| **OCPU** | *Oracle CPU* — one physical core (≈ 2 vCPUs); the unit Flex shapes are sized in. |
| **CCSP** | *(Red Hat) Certified Cloud & Service Provider* — the program certifying Oracle Linux images; a df=2 OOV probe. |
| **KVM** | *Kernel-based Virtual Machine* — the Linux virtualization / guest-image format; an OOV probe. |

---

## Retrieval / RAG / ML concepts

| Term | Meaning |
|---|---|
| **RAG** | *Retrieval-Augmented Generation* — the overall pattern: retrieve relevant document chunks, then feed them to an LLM to generate a grounded, cited answer. This whole project is a RAG system. |
| **LLM** | *Large Language Model* — here, Claude Sonnet 4.6 (the answer generator) and the judge model. |
| **chunk / chunking** | Splitting source documents into smaller passages (here 400–800 tokens) so each can be embedded and retrieved independently. A *chunk* is one such passage. |
| **token** | The unit text is split into for models (roughly ¾ of a word). Chunk sizes, embedding cost, and LLM cost are all measured in tokens. |
| **embedding** | A numeric vector representing the meaning of a chunk, so semantically similar text lands near it in vector space. Produced here by Voyage `voyage-3-large` (1024-dimensional). |
| **vector / vector search** | The embedding is a *vector*; *vector search* finds chunks whose vectors are closest to the query's vector (semantic match). |
| **1024-d / 1024-dim** | The embedding vectors have 1024 dimensions — this fixes the vector size of the Qdrant collections. |
| **cosine (similarity)** | The distance metric used to compare vectors — measures the angle between them. Qdrant collections use cosine. |
| **BM25** | *Best Matching 25* — a classic keyword-ranking algorithm (term frequency based). The "lexical"/exact-word half of hybrid search, served by SQLite FTS5. Good at exact identifiers/codes that vectors can miss. |
| **hybrid (retrieval)** | Combining vector search + BM25 keyword search, then fusing the two ranked lists. Catches both semantic and exact-keyword matches. |
| **RRF** | *Reciprocal Rank Fusion* — the formula that merges the vector and BM25 ranked lists into one (uses each item's rank, with a constant `k=60`). |
| **rerank / reranker** | A second-pass model that re-scores a candidate pool for relevance to the query. Here Voyage `rerank-2` re-orders 30 hybrid candidates down to the top_k — boosts precision, especially on hard queries. |
| **top_k** | The number of results returned (the *k* best). |
| **recall** | Of all the chunks that *should* be found for a query, what fraction were actually retrieved — a **completeness** metric. |
| **recall@k** | Recall measured within the top *k* results. **recall@1** = was the single best result correct (top-1 precision). |
| **precision** | Of the chunks returned, what fraction were actually relevant (the inverse concern to recall — how much junk). |
| **MRR** | *Mean Reciprocal Rank* — averages 1/(rank of the first relevant result) across queries; rewards putting the right chunk higher in the list. |
| **adversarial (questions)** | Deliberately tricky eval questions (terminology lures) designed to stress retrieval and routing. One of the **four** eval categories — single / cross / adversarial / exact_term — balanced 15 questions each (60 total). |
| **exact_term** | The fourth eval category: minimal-context lookups of exact identifiers / codes / member names (the BM25-favourable, "regime A" case). Scored on the TEXT bar since its tokens live in chunk *body*, not section headings. |
| **LLM-as-judge** | Using an LLM (Sonnet 4.6) to score the quality of generated answers in the eval, instead of only string-matching. |
| **federation / federated** | Architecture where each product's docs live behind its own isolated MCP server, and the orchestrator routes a query to the right one(s) — rather than one big merged index. |
| **routing** | The orchestrator deciding which product server(s) a question belongs to (single-product, cross-product, or none/out-of-scope). |
| **orchestrator** | The agent (Claude loop) that receives the user question, routes it, calls the MCP tools, and synthesizes the final answer. |
| **tool-use loop** | The agent pattern where Claude calls a tool, gets the result, and loops until it can answer — implemented manually here to capture a structured trace. |
| **prompt caching** | Anthropic feature that caches a stable prompt prefix (system + tools) so repeated calls are cheaper/faster. Cache-**write** (first time) costs slightly more; cache-**read** (reuse) is much cheaper. |
| **manifest** | A declarative index file (`_index.json`) listing every source document and how to fetch it; drives the ingestion pipeline. |
| **ingestion (pipeline)** | The fetch → chunk → embed → store sequence that builds the knowledge base. |
| **section_path** | The heading breadcrumb of a chunk (e.g. `2 Journals > Reverse Journals`), used as a structured label and a strict relevance bar. |
| **retrieval modes** | The four modes compared in the ablation: **vector_only** (Qdrant semantic), **keyword_only** (BM25/FTS5 only), **hybrid** (vector+BM25 fused), **hybrid_rerank** (hybrid candidates re-scored by `rerank-2` — the production default). |
| **TF-IDF / IDF** | *Term Frequency–Inverse Document Frequency*; **IDF** weights a token by how *rare* it is across the corpus. The intuition behind BM25 (rare tokens are distinctive) and the IDF adaptive-router signal. |
| **document frequency (DF)** | How many chunks a token appears in. Low DF = rare/distinctive (e.g. `XCC` at df=2); the IDF router routes low-DF queries toward BM25. Global DF (whole corpus) separates distinctive tokens from off-domain lure words better than product-scoped DF. |
| **OOV** | *Out-Of-Vocabulary* — tokens the embedder can't represent well (rare codes/acronyms). The regime where dense retrieval genuinely *fails on recall* (not just ranking) and BM25 must carry it; demonstrated in `evals/oov_slice.py`. |
| **regime / regime-dependent** | A query type with its own retrieval behaviour (semantic / exact-term / OOV / adversarial). Headline finding: no single retriever wins every regime — the choice of retriever, fusion, and router signal are all regime-dependent. |
| **complementarity** | How much one retrieval leg finds that the other misses. Hybrid's gain is bounded by it — near-zero on this clean corpus (keyword *uniquely* rescued only 2/75 queries). |
| **weighted fusion** | Score-based fusion combining the legs as **α·vector + (1−α)·BM25** over min-max-normalized scores — unlike RRF it preserves score *magnitude* and can weight the stronger leg. |
| **adaptive fusion / router** | Choosing the fusion weight **α** *per query*: identifier / rare-token ("lexical") queries lean BM25; semantic queries lean vector. Signal is `regex` (token structure), `idf` (rarity), or `both`. |
| **α (alpha)** | The vector-leg weight in weighted/adaptive fusion: **1.0** = pure vector, **0** = pure BM25. At α=1.0 the adaptive hybrid degenerates to vector for semantic queries (a dense-vs-sparse router). |
| **lexical (query)** | A query best served by exact-token matching (an identifier, code, or rare term) rather than semantic similarity — what the adaptive router tries to detect. |
| **min-max normalization** | Rescaling each leg's scores to [0,1] over its own candidates, so cosine (vector) and BM25 magnitudes become comparable before weighted fusion. |
| **cross-encoder** | A reranker that reads query + document *together* to score relevance (vs the bi-encoder embeddings, scored independently). Voyage `rerank-2` is a cross-encoder; reranking the union beats RRF-ing it. |
| **candidate pool** | The first-stage results fed to the reranker (here 30, `RERANK_CANDIDATES`); `RERANK_POOL` selects whether they're drawn from vector-only or the hybrid (RRF) list. `RERANK_INCLUDE_PATH` prepends each chunk's section path to the reranker input. |
| **relevance floor** | A minimum rerank score (`MIN_RERANK_SCORE`); results below it are dropped so the agent can **abstain** ("I don't know") rather than answer from off-topic chunks. |
| **TEXT bar / SECTION bar** | The two eval relevance bars — **TEXT** = keyword anywhere in the chunk (lenient); **SECTION** = keyword in the chunk's section path (strict, "right section"). exact_term is scored on the TEXT bar. |
| **ablation** | Measuring each component's contribution by toggling it on/off (keyword vs vector vs hybrid vs rerank) — makes the design *measured*, not asserted. |
| **nDCG** | *normalized Discounted Cumulative Gain* — a graded ranking metric rewarding relevant docs near the top; the set-recall-style metric proposed to measure hybrid's union-of-relevant-docs benefit. |
| **Pareto-dominant** | Better on at least one regime and no worse on any other. The adaptive hybrid (sem α=1.0) is Pareto-dominant over pure vector — ties the semantic regimes, wins exact_term. |
| **dilution** | Fusion dragging a confident strong-leg hit *down* by blending in the weaker leg — why naive RRF lands *between* its two legs and below the stronger one. |

---

## Databases & storage

| Term | Meaning |
|---|---|
| **Qdrant** | The vector database storing chunk embeddings; one collection per product. Used for semantic (vector) search. |
| **SQLite** | A lightweight file-based SQL database; holds the chunk text and metadata. |
| **FTS5** | *Full-Text Search v5* — SQLite's built-in full-text index extension; provides the BM25 keyword-search half of hybrid retrieval. |
| **external-content (FTS)** | An FTS5 mode where the index points at an existing table rather than duplicating its content (kept in sync by triggers). |
| **upsert** | "**Up**date or in**sert**" — write a row, inserting if new or overwriting if a record with the same id exists. |
| **idempotent** | An operation that has the same effect run once or many times — re-running ingest never duplicates chunks (deterministic ids + upsert). |
| **UUID / UUID5** | *Universally Unique Identifier*; **UUID5** derives the id deterministically by hashing a name, so the same chunk always gets the same id (enables idempotent upsert). |
| **collection** | Qdrant's term for a named set of vectors (here, one per product: `erp_docs`, `epm_docs`, `oci_docs`). |

---

## Infrastructure, DevOps & networking

| Term | Meaning |
|---|---|
| **VPS** | *Virtual Private Server* — the Ubuntu cloud machine (4 vCPU, 16 GB RAM) that is the deploy/runtime target. |
| **vCPU** | *Virtual CPU* — a virtualized processor core allocated to the VPS. |
| **RAM** | *Random-Access Memory* — the server's working memory (16 GB here). |
| **dev container** | The Docker-based development environment (`/workspaces/...`) where code is authored — distinct from the VPS where it runs. |
| **Docker Compose** | Tool to define and run multi-container apps from a `docker-compose.yml` (Qdrant, the MCP servers, orchestrator, UI, etc.). |
| **container / image** | A *container* is a running isolated process; an *image* is the built template it runs from. "Rebuild the image" = bake in code changes. |
| **Traefik** | The reverse proxy / ingress already running on the host; handles TLS and routing to containers via labels. Not modified by this project. |
| **reverse proxy / ingress** | The component that receives public web traffic and forwards it to the right internal container. |
| **DNS** | *Domain Name System* — maps the hostname (`navigator.p36server.com`) to the server's IP. An **A record** is the hostname→IPv4 mapping. |
| **TLS** | *Transport Layer Security* — the encryption behind HTTPS. Traefik terminates TLS (here via the `mytlschallenge` resolver). |
| **SSH** | *Secure Shell* — encrypted remote login, used to reach the VPS for deploy actions. |
| **basic auth** | HTTP Basic Authentication — a simple username/password gate (here `demo`/`demo`) applied by Traefik. |
| **htpasswd** | A utility that generates the hashed user:password entries used by basic auth. |
| **bcrypt** | A password-hashing algorithm; the basic-auth password is stored as a bcrypt hash, not plaintext. |
| **`.env` / `.env.example`** | The `.env` file holds real secrets (API keys) and is gitignored; `.env.example` is the committed placeholder template. |
| **env_file / env var** | *Environment variable* — config passed to a container (e.g. `ANTHROPIC_API_KEY`) at runtime. |
| **bind mount** | Mapping a host directory/file into a container; `:ro` makes it **read-only**. |
| **`:ro` / read-only** | The SQLite DB is mounted read-only into the MCP servers so they can't modify it. |
| **healthcheck / `/health`** | An endpoint/probe Docker uses to confirm a container is `Up (healthy)`. |
| **spend cap** | A hard dollar limit set on the Anthropic/Voyage dashboards to prevent runaway API cost. |
| **internal network** | A Docker network not exposed publicly; everything except the UI stays on it. |

---

## Frameworks, tools & libraries

| Term | Meaning |
|---|---|
| **MCP** | *Model Context Protocol* — Anthropic's open standard for exposing tools/data to an LLM agent. Each product runs an **MCP server**; the orchestrator is the **MCP client**. |
| **streamable-HTTP** | The MCP transport used here (tools served over HTTP, supporting streaming) — as opposed to stdio. |
| **FastMCP** | A high-level helper in the MCP SDK for building MCP servers quickly. |
| **SDK** | *Software Development Kit* — a client library; e.g. the **Anthropic SDK** (used directly, no LangChain) and the **MCP SDK**. |
| **API** | *Application Programming Interface* — how programs talk to a service (the Anthropic/Voyage HTTP APIs; the orchestrator's `/query` API). |
| **Batches API** | Anthropic's bulk endpoint for running many LLM calls cheaply/asynchronously — used by the eval judge. |
| **FastAPI** | The Python web framework serving the orchestrator's `/query` endpoint. |
| **uvicorn** | The ASGI web server that runs the FastAPI app (on port 8000). |
| **Streamlit** | The Python framework for the demo UI (the public web app, port 8501). |
| **Voyage** | Voyage AI — the embedding + rerank provider (`voyage-3-large` embeddings, `rerank-2` reranker). |
| **Claude Sonnet 4.6** | The Anthropic LLM (`claude-sonnet-4-6`) powering the orchestrator agent and the eval judge. |
| **Anthropic** | The company providing the Claude models / API. |
| **LangChain / LangGraph** | Popular agent-orchestration frameworks — **deliberately not used** here (the Anthropic SDK is used directly). |
| **tiktoken** | The tokenizer library used to count tokens; `cl100k_base` is its encoding. |
| **pymupdf** | The PDF-parsing library (used to extract text + detect headings by font size). |
| **beautifulsoup4 / lxml** | Libraries for parsing/crawling HTML (the OCI pages). |
| **httpx** | An async HTTP client (fetching docs, calling APIs). |
| **pydantic** | A data-validation/modeling library (`Chunk`, `Document`, `SearchResult`). |
| **gitignored** | Listed in `.gitignore` so the file is never committed (keeps secrets and local-only docs out of the public repo). |

---

## Protocols, formats & web terms

| Term | Meaning |
|---|---|
| **HTTP / HTTP 200** | *HyperText Transfer Protocol*; **200** = the "OK/success" status code. **HTTP 500** = server error. |
| **HTML / `.htm`** | *HyperText Markup Language* — the web-page format crawled for the OCI corpus. |
| **PDF** | *Portable Document Format* — the format of the ERP/EPM source guides. |
| **JSON / JSONL** | *JavaScript Object Notation*; **JSONL** = JSON Lines (one JSON object per line), used for the eval dataset and results. |
| **URL** | *Uniform Resource Locator* — a web address. |
| **SaaS** | *Software as a Service* — cloud-hosted applications (Fusion is SaaS; its HTML guides are JS-rendered, which is why ERP/EPM were sourced as PDFs). |
| **TOC** | *Table of Contents*. |
| **SSE** | *Server-Sent Events* — a one-way streaming protocol; powers `POST /query/stream` so answers stream token-by-token. |
| **CLI** | *Command-Line Interface* — the terminal tools/smoke tests (`python -m ...`). |
| **UI** | *User Interface* — the Streamlit demo front-end. |
| **websocket** | A persistent two-way browser↔server connection (Streamlit uses these; they pass through Traefik). |
| **XSRF / CSRF** | *Cross-Site Request Forgery* — a web attack class; Streamlit's protection (`enableXsrfProtection`) was left on. |

---

## Project / process shorthand

| Term | Meaning |
|---|---|
| **DRY** | *Don't Repeat Yourself* — avoid duplicating logic (e.g. server scope flows from one source into the prompt). |
| **p95** | The 95th-percentile value — 95% of measurements are at or below it (used for latency: p95 ≈ 323 ms). |
| **latency** | Time taken to respond, in milliseconds (`latency_ms`). |
| **smoke test** | A quick check that a module basically works (`python -m shared.chunking`) without running the whole stack. |
| **scorecard / eval** | The evaluation harness measuring retrieval and answer quality. |
| **trace** | The structured record of what the agent did (which servers/tools, which chunks, per-step latency) — surfaced in the UI. |
| **namespacing** | Prefixing tool names per product (`erp_`, `oci_`, `epm_`) so the agent can tell identical tools apart. |
| **definition of done** | The explicit checklist each phase must satisfy before moving on. |
| **superseded** | Marked as replaced by newer information (e.g. the original Phase 1 chunk counts after the re-ingest). |
| **deterministic** | Always produces the same output for the same input (e.g. UUID5 ids). |
| **stretch goal** | An optional, nice-to-have feature beyond the core plan. |
