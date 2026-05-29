# Streamlit demo UI — the only publicly exposed service (via Traefik).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY ui/requirements.txt ./ui/requirements.txt
RUN pip install --no-cache-dir -r ui/requirements.txt

COPY ui ./ui

EXPOSE 8501

# Streamlit's built-in health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=3).status==200 else 1)"

CMD ["streamlit", "run", "ui/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
