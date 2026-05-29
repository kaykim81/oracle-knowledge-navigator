"""Oracle Knowledge Navigator — Streamlit demo UI.

Single page: ask a question, see the cited answer and the agent trace (which MCP
server(s) were called, the top chunks each returned, and per-step latency). The
trace is the point of the demo — it makes federation visible.

Talks to the orchestrator over HTTP (POST /query). Run with:
    streamlit run ui/app.py
"""

from __future__ import annotations

import os

import requests
import streamlit as st

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
REPO_URL = "https://github.com/kaykim81/oracle-knowledge-navigator"
REQUEST_TIMEOUT = 240

SAMPLES = {
    "ERP — single product": "How do I reverse a posted journal entry?",
    "OCI — single product": "How do I create an Object Storage bucket in OCI?",
    "EPM — single product": "How do I run a consolidation in EPM?",
    "Cross-product (ERP → EPM)": "How does data flow from Fusion ERP into EPM Financial Consolidation and Close?",
}

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
    st.markdown(f"📊 **Eval scorecard:** see `evals/results/` in the [repo]({REPO_URL})")
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

st.write("**Try a sample:**")
cols = st.columns(len(SAMPLES))
for col, (label, sample) in zip(cols, SAMPLES.items()):
    if col.button(label, use_container_width=True):
        st.session_state.question = sample

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


# --------------------------------------------------------------------------- #
# Query + render
# --------------------------------------------------------------------------- #
if ask and question.strip():
    try:
        with st.spinner("Routing across the federated knowledge bases…"):
            resp = requests.post(
                f"{ORCHESTRATOR_URL}/query",
                json={"question": question},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
    except requests.RequestException as exc:
        st.error(f"Could not reach the orchestrator: {exc}")
        st.stop()

    trace = data.get("trace", [])
    servers = sorted({step["server"].upper() for step in trace})

    st.markdown("### Answer")
    st.markdown(data.get("answer", "_(no answer)_"))

    routed = ", ".join(servers) if servers else "no knowledge base needed (out of scope)"
    st.info(f"**Routed to:** {routed}  ·  **{len(trace)} tool call(s)**  ·  "
            f"**{data.get('latency_ms', 0):.0f} ms** total")

    with st.expander(f"🛠️ Agent trace — {len(trace)} tool call(s)", expanded=True):
        if not trace:
            st.write("No tools were called — the question was handled without retrieval.")
        for i, step in enumerate(trace, 1):
            st.markdown(
                f"**{i}. `{step['server']}_{step['tool']}`** · "
                f"{step.get('num_results', '—')} results · {step['latency_ms']:.0f} ms"
            )
            st.caption(f"args: `{step['args']}`")
            with st.expander("results", expanded=(step["tool"] == "search_docs")):
                render_results(step.get("results"))
