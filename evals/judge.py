"""LLM-as-judge — scores answers with Claude Sonnet 4.6 via the Batches API.

Each (question, answer, retrieved-chunks) triple is scored 1-5 on correctness,
groundedness, and citation quality against a strict rubric. The Batches API is
half price and well-suited to scoring ~135 rows at once.
"""

from __future__ import annotations

import json
import logging
import time

import anthropic

log = logging.getLogger("evals.judge")

MODEL = "claude-sonnet-4-6"

RUBRIC = """You are a strict evaluator of answers from an Oracle documentation assistant.
Score the answer on three axes, each an integer 1-5:

CORRECTNESS — does the answer correctly address what the question asked?
  1 = wrong or irrelevant. 3 = partially correct or incomplete. 5 = fully correct and complete.
GROUNDEDNESS — is every claim supported by the retrieved chunks provided?
  1 = mostly unsupported / hallucinated. 3 = partly supported. 5 = every claim traceable to a chunk.
CITATION_QUALITY — does the answer cite specific sources (section paths / source URLs)?
  1 = no citations. 3 = vague or partial citations. 5 = specific, relevant citations.

Be critical: reserve 5 for genuinely excellent answers. If the retrieved chunks are empty or
off-topic, groundedness cannot be high. Respond with ONLY a JSON object, no prose:
{"correctness": <1-5>, "groundedness": <1-5>, "citation_quality": <1-5>, "rationale": "<one sentence>"}"""

_ZERO = {"correctness": 0, "groundedness": 0, "citation_quality": 0, "rationale": "no judgment"}


def _prompt(row: dict) -> str:
    # Judge groundedness against the FULL chunk text the model actually saw, not
    # the 200-char UI snippet — otherwise larger chunks look ungrounded because
    # the supporting sentence sits past the truncation.
    chunks = "\n".join(
        f"- [{c.get('score')}] {' > '.join(c.get('section_path') or [])}: "
        f"{c.get('text') or c.get('snippet', '')}"
        for c in (row.get("retrieved_chunks") or [])[:8]
    ) or "(no chunks retrieved)"
    return (
        f"QUESTION:\n{row['question']}\n\n"
        f"ANSWER TO EVALUATE:\n{row.get('answer', '')}\n\n"
        f"RETRIEVED CHUNKS (the answer should be grounded in these):\n{chunks}"
    )


def _parse(text: str) -> dict:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                pass
    return dict(_ZERO, rationale="unparseable judge output")


def judge_rows(
    rows: list[dict], *, client: anthropic.Anthropic | None = None,
    poll_interval: int = 20, timeout: int = 3600,
) -> list[dict]:
    """Score each row. Returns a list of judgment dicts aligned to `rows`."""
    if not rows:
        return []
    client = client or anthropic.Anthropic()
    requests = [
        {
            "custom_id": str(i),
            "params": {
                "model": MODEL,
                "max_tokens": 512,
                "system": RUBRIC,
                "messages": [{"role": "user", "content": _prompt(row)}],
            },
        }
        for i, row in enumerate(rows)
    ]

    batch = client.messages.batches.create(requests=requests)
    log.info("judge batch %s created: %d requests", batch.id, len(requests))

    deadline = time.time() + timeout
    while True:
        status = client.messages.batches.retrieve(batch.id).processing_status
        if status == "ended":
            break
        if time.time() > deadline:
            raise TimeoutError(f"judge batch {batch.id} did not finish within {timeout}s")
        time.sleep(poll_interval)

    scores: dict[str, dict] = {}
    for result in client.messages.batches.results(batch.id):
        if result.result.type == "succeeded":
            text = next((b.text for b in result.result.message.content if b.type == "text"), "")
            scores[result.custom_id] = _parse(text)
        else:
            scores[result.custom_id] = dict(_ZERO, rationale=f"judge {result.result.type}")
    return [scores.get(str(i), dict(_ZERO)) for i in range(len(rows))]
