# On-demand eval runner: queries the orchestrator across retrieval modes, judges,
# and writes a results JSONL + markdown summary. Run: docker compose run --rm evals
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY evals/requirements.txt ./evals/requirements.txt
RUN pip install --no-cache-dir -r evals/requirements.txt

COPY evals ./evals

CMD ["python", "-m", "evals.runner"]
