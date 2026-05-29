"""Orchestrator agent — the routing brain.

Connects to the three product MCP servers as a client, discovers their tools and
namespaces them per product (``erp_search_docs``, ``oci_search_docs``, …), then
runs a Claude tool-use loop: Claude decides which product(s) to query, the
orchestrator forwards each call to the right MCP server, and the loop continues
until Claude produces a final cited answer.

``query()`` returns ``{answer, trace, latency_ms}`` where ``trace`` records every
tool call (which server, args, a result preview, per-step latency) — this is what
makes federation visible in the UI.

Uses the Anthropic SDK directly (no LangChain/LangGraph), Claude Sonnet 4.6, with
prompt caching on the stable system+tools prefix.

CLI (needs the MCP servers reachable + ANTHROPIC_API_KEY)::

    python -m orchestrator.agent --question "how do I reverse a journal entry?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from contextlib import AsyncExitStack
from pathlib import Path

from anthropic import AsyncAnthropic
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = logging.getLogger("orchestrator")

MODEL = "claude-sonnet-4-6"  # locked by the project (PROJECT_CONTEXT.md)
MAX_TOKENS = 4096
MAX_STEPS = 8  # safety bound on the tool-use loop
_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"

# Product -> MCP endpoint (service names on the internal Docker network).
DEFAULT_SERVERS: dict[str, str] = {
    "erp": os.getenv("ERP_MCP_URL", "http://erp-mcp:8001/mcp"),
    "oci": os.getenv("OCI_MCP_URL", "http://oci-mcp:8002/mcp"),
    "epm": os.getenv("EPM_MCP_URL", "http://epm-mcp:8003/mcp"),
}


def _tool_result_payload(result) -> str:
    """Serialize an MCP CallToolResult into text to hand back to Claude."""
    if result.structuredContent is not None:
        sc = result.structuredContent
        data = sc.get("result", sc) if isinstance(sc, dict) else sc
        return json.dumps(data)
    parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    return "\n".join(parts) if parts else "[]"


def _result_preview(payload: str, limit: int = 280) -> tuple[str, int | None]:
    """A short preview of a tool result + a count if it's a list, for the trace."""
    count = None
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            count = len(parsed)
    except (ValueError, TypeError):
        pass
    preview = payload if len(payload) <= limit else payload[:limit] + "…"
    return preview, count


class OrchestratorAgent:
    """Holds persistent MCP sessions and runs the Claude tool-use loop."""

    def __init__(self, servers: dict[str, str] | None = None, *, model: str = MODEL):
        self.servers = servers or DEFAULT_SERVERS
        self.model = model
        self._anthropic = AsyncAnthropic()
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tools: list[dict] = []          # namespaced tool defs for Claude
        self._routing: dict[str, tuple[str, str]] = {}  # tool name -> (product, orig)
        self._system: str = ""

    async def connect(self) -> None:
        """Open sessions to all servers, discover + namespace their tools."""
        base_prompt = _PROMPT_PATH.read_text()
        scope_sections: list[str] = []

        for product, url in self.servers.items():
            read, write, _ = await self._stack.enter_async_context(streamablehttp_client(url))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            init = await session.initialize()
            self._sessions[product] = session

            tools = (await session.list_tools()).tools
            names = []
            for tool in tools:
                namespaced = f"{product}_{tool.name}"
                self._tools.append({
                    "name": namespaced,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                })
                self._routing[namespaced] = (product, tool.name)
                names.append(namespaced)

            instructions = (init.instructions or "").strip()
            scope_sections.append(
                f"### {product.upper()} — tools: {', '.join(names)}\n{instructions}"
            )
            log.info("connected to %s (%s): %d tools", product, url, len(tools))

        self._system = base_prompt + "\n" + "\n\n".join(scope_sections) + "\n"

    async def aclose(self) -> None:
        await self._stack.aclose()

    async def query(
        self, question: str, *, retrieval_mode: str | None = None, max_steps: int = MAX_STEPS
    ) -> dict:
        """Run the tool-use loop for one question. Returns {answer, trace, latency_ms}."""
        t0 = time.perf_counter()
        # cache_control on the (stable) system block caches tools+system across queries
        system = [{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}]
        messages: list[dict] = [{"role": "user", "content": question}]
        trace: list[dict] = []
        answer = ""

        for _ in range(max_steps):
            resp = await self._anthropic.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=self._tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})
            answer = "".join(b.text for b in resp.content if b.type == "text")

            if resp.stop_reason != "tool_use":
                break

            tool_results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                product, orig = self._routing[block.name]
                args = dict(block.input)
                # Force the retrieval mode when the caller pins one (eval comparison).
                if retrieval_mode and orig == "search_docs":
                    args["mode"] = retrieval_mode

                ts = time.perf_counter()
                result = await self._sessions[product].call_tool(orig, args)
                step_ms = round((time.perf_counter() - ts) * 1000, 1)

                payload = _tool_result_payload(result)
                preview, count = _result_preview(payload)
                trace.append({
                    "server": product, "tool": orig, "args": args,
                    "num_results": count, "result_preview": preview, "latency_ms": step_ms,
                })
                tool_results.append({
                    "type": "tool_result", "tool_use_id": block.id, "content": payload,
                })
            messages.append({"role": "user", "content": tool_results})

        return {
            "answer": answer,
            "trace": trace,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


# --------------------------------------------------------------------------- #
# CLI (needs MCP servers reachable + ANTHROPIC_API_KEY)
# --------------------------------------------------------------------------- #


async def _main() -> None:
    ap = argparse.ArgumentParser(description="Query the orchestrator agent")
    ap.add_argument("--question", required=True)
    ap.add_argument("--mode", default=None, help="pin retrieval mode for search_docs")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    agent = OrchestratorAgent()
    await agent.connect()
    try:
        out = await agent.query(args.question, retrieval_mode=args.mode)
    finally:
        await agent.aclose()

    print("\n=== ANSWER ===\n" + out["answer"])
    print(f"\n=== TRACE ({out['latency_ms']} ms total) ===")
    for i, step in enumerate(out["trace"], 1):
        print(f"{i}. {step['server']}.{step['tool']}({step['args']}) "
              f"-> {step['num_results']} results in {step['latency_ms']} ms")


if __name__ == "__main__":
    asyncio.run(_main())
