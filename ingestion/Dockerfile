# On-demand ingestion tool: fetch Oracle docs, chunk, embed, write to
# Qdrant + SQLite. Run with `docker compose run --rm ingestion`.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Dependencies first (better layer caching).
COPY ingestion/requirements.txt ./ingestion/requirements.txt
RUN pip install --no-cache-dir -r ingestion/requirements.txt

# Pre-warm the tiktoken encoding so ingestion doesn't download it at runtime.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# Application code (shared library + ingestion scripts; _index.json comes with it,
# raw sources are re-fetched at run time — see .dockerignore).
COPY shared ./shared
COPY ingestion ./ingestion

# Default: full fetch, then full ingest. Override by passing a command, e.g.
#   docker compose run --rm ingestion python -m ingestion.ingest --dry-run
CMD ["sh", "-c", "python -m ingestion.fetch_docs && python -m ingestion.ingest"]
