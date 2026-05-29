# ERP MCP server — Oracle Fusion Cloud ERP knowledge over MCP streamable-HTTP.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY mcp_servers/erp/requirements.txt ./mcp_servers/erp/requirements.txt
RUN pip install --no-cache-dir -r mcp_servers/erp/requirements.txt

COPY shared ./shared
COPY mcp_servers/erp ./mcp_servers/erp

EXPOSE 8001

# Liveness via the /health route (no curl in slim image; use stdlib urllib).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8001/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "mcp_servers.erp.server"]
