"""Oracle Knowledge Navigator — Streamlit demo UI.

Single page: ask a question, see the cited answer and the agent trace (which MCP
server(s) were called, the top chunks each returned, and per-step latency). The
trace is the point of the demo — it makes federation visible.

Talks to the orchestrator over HTTP, streaming Server-Sent Events from
POST /query/stream so the trace builds live and the answer streams in. Run with:
    streamlit run ui/app.py
"""

from __future__ import annotations

import json
import os
import random

import requests
import streamlit as st

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
REPO_URL = "https://github.com/kaykim81/oracle-knowledge-navigator"
EVALS_URL = f"{REPO_URL}/tree/main/evals/results"
REQUEST_TIMEOUT = 240

# Each button draws a fresh question at random from its pool on every click
# (see pick_sample). Cross-product questions exercise federation across servers.
SAMPLES = {
    "ERP — single product": [
        "How do I reverse a posted journal entry?",
        "How do I set up an allocation rule in General Ledger?",
        "How do I record a manual payment for a supplier invoice?",
        "How do I process a customer receipt in Accounts Receivable?",
        "How do I run depreciation for a fixed asset?",
    ],
    "OCI — single product": [
        "How do I create an Object Storage bucket in OCI?",
        "How do I launch a compute instance?",
        "How do I create a VCN with public and private subnets?",
        "How do I write an IAM policy to grant access to a compartment?",
        "What Object Storage tiers are available and when should I use each?",
    ],
    "EPM — single product": [
        "How do I run a consolidation in EPM?",
        "How do I translate balances to a parent currency during the close?",
        "How do I create a data form in Planning?",
        "How do I set up an allocation rule in Planning?",
        "How do I manage intercompany eliminations in Financial Consolidation and Close?",
    ],
    "Cross-product (ERP → EPM)": [
        "How does data flow from Fusion ERP into EPM Financial Consolidation and Close?",
        "How do ERP General Ledger balances get loaded into EPM for consolidation?",
        "How are intercompany transactions handled across ERP and EPM?",
        "How does the period close coordinate between Fusion ERP and EPM?",
    ],
}


def pick_sample(label: str) -> str:
    """Pick a random question from a button's pool, avoiding an immediate repeat."""
    pool = SAMPLES[label]
    last = st.session_state.get("_last_sample", {}).get(label)
    choices = [q for q in pool if q != last] or pool
    chosen = random.choice(choices)
    st.session_state.setdefault("_last_sample", {})[label] = chosen
    return chosen

st.set_page_config(page_title="Oracle Knowledge Navigator", page_icon="🔎", layout="wide")


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Why federation?")
    st.write(
        "Each Oracle product line is its own MCP server with its own knowledge base. "
        "The orchestrator routes every question to the right server(s) — the user never "
        "needs to know which knowledge base holds the answer. This is the pattern that "
        "scales across many Oracle product lines."
    )
    st.divider()
    st.markdown(f"📊 **Eval scorecard:** [`evals/results/`]({EVALS_URL}) in the repo")
    st.markdown(f"💻 **Source:** [{REPO_URL.split('//')[1]}]({REPO_URL})")


# --------------------------------------------------------------------------- #
# Header + question input
# --------------------------------------------------------------------------- #
st.title("🔎 Oracle Knowledge Navigator")
st.caption(
    "A federated MCP demo. Ask about Oracle **ERP** (Financials), **OCI** (infrastructure), "
    "or **EPM** — a single question can span products."
)

if "question" not in st.session_state:
    st.session_state.question = ""

st.write("**Try a sample** (each button picks a fresh question):")
cols = st.columns(len(SAMPLES))
for col, label in zip(cols, SAMPLES):
    if col.button(label, use_container_width=True):
        st.session_state.question = pick_sample(label)

question = st.text_input("Your question", key="question", placeholder="Ask about Oracle ERP, OCI, or EPM…")
ask = st.button("Ask", type="primary")


# --------------------------------------------------------------------------- #
# Trace rendering
# --------------------------------------------------------------------------- #
def render_results(results) -> None:
    """Render the per-tool-call results (top chunks / topics / document header)."""
    if isinstance(results, list):
        for r in results:
            if isinstance(r, dict) and "snippet" in r:  # search_docs chunk
                path = " › ".join(r.get("section_path") or []) or "(no section)"
                url = r.get("source_url", "")
                st.markdown(f"- **[{r.get('score')}]** {path} — [source]({url})")
                st.caption(r.get("snippet", ""))
            else:  # list_topics -> strings
                st.markdown(f"- {r}")
    elif isinstance(results, dict):  # get_document header
        st.markdown(
            f"**{results.get('title')}** — {results.get('chars')} chars — "
            f"[source]({results.get('source_url')})"
        )
        st.caption(results.get("snippet", ""))
    else:
        st.code(str(results))


def render_trace(container, trace: list) -> None:
    """(Re)render the whole trace into a container — called as it builds live."""
    container.empty()
    with container.container():
        if not trace:
            st.caption("Routing… (no tool calls yet)")
        for i, step in enumerate(trace, 1):
            st.markdown(
                f"**{i}. `{step['server']}_{step['tool']}`** · "
                f"{step.get('num_results', '—')} results · {step['latency_ms']:.0f} ms"
            )
            st.caption(f"args: `{step['args']}`")
            with st.expander("results", expanded=(step["tool"] == "search_docs")):
                render_results(step.get("results"))


def render_cost_panel(cost: dict) -> None:
    """Show the per-question Claude cost: USD, output tokens, and cache savings.

    Tracks the orchestrator's LLM spend only — the Voyage embed/rerank cost lives
    in the MCP servers and is not counted here. Labelled accordingly so the figure
    is honest rather than implying it's the full bill.
    """
    in_tok = cost.get("input_tokens", 0)
    out_tok = cost.get("output_tokens", 0)
    cache_read = cost.get("cache_read_input_tokens", 0)
    cache_write = cost.get("cache_creation_input_tokens", 0)
    # Fraction of *input* tokens served from cache (read / all input seen).
    total_input = in_tok + cache_read + cache_write
    cache_pct = (cache_read / total_input) if total_input else 0.0

    st.markdown("#### 💰 Cost & tokens")
    c1, c2, c3 = st.columns(3)
    c1.metric("LLM cost", f"${cost.get('usd', 0):.4f}")
    c2.metric("Output tokens", f"{out_tok:,}")
    c3.metric("Input from cache", f"{cache_pct:.0%}")
    st.caption(
        f"`{cost.get('model', 'claude')}` · in {in_tok:,} · out {out_tok:,} · "
        f"cache-read {cache_read:,} · cache-write {cache_write:,} tok  ·  "
        "Claude only — Voyage embed/rerank (in the MCP servers) not counted."
    )


def stream_query(question: str):
    """Yield SSE events from the orchestrator's /query/stream endpoint."""
    with requests.post(
        f"{ORCHESTRATOR_URL}/query/stream",
        json={"question": question},
        stream=True,
        timeout=REQUEST_TIMEOUT,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                yield json.loads(line[len("data: "):])


# --------------------------------------------------------------------------- #
# Query + render (streamed: trace builds live, answer streams in)
# --------------------------------------------------------------------------- #
if ask and question.strip():
    st.markdown("### Answer")
    answer_box = st.empty()
    info_box = st.empty()
    st.markdown("#### 🛠️ Agent trace")
    trace_box = st.empty()

    trace: list = []
    answer = ""
    latency_ms = 0
    cost: dict = {}
    render_trace(trace_box, trace)

    try:
        with st.spinner("Routing across the federated knowledge bases…"):
            for event in stream_query(question):
                kind = event.get("type")
                if kind == "tool_call":
                    trace.append(event)
                    render_trace(trace_box, trace)
                elif kind == "answer_delta":
                    answer += event.get("text", "")
                    answer_box.markdown(answer + " ▌")  # cursor while streaming
                elif kind == "done":
                    latency_ms = event.get("latency_ms", 0)
                    trace = event.get("trace", trace)
                    cost = event.get("cost", {})
    except requests.RequestException as exc:
        st.error(f"Could not reach the orchestrator: {exc}")
        st.stop()

    answer_box.markdown(answer or "_(no answer)_")
    render_trace(trace_box, trace)

    servers = sorted({step["server"].upper() for step in trace})
    routed = ", ".join(servers) if servers else "no knowledge base needed (out of scope)"
    info_box.info(f"**Routed to:** {routed}  ·  **{len(trace)} tool call(s)**  ·  "
                  f"**{latency_ms:.0f} ms** total")

    if cost:
        render_cost_panel(cost)
