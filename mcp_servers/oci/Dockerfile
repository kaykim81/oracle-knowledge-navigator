# OCI MCP server — Oracle Cloud Infrastructure knowledge over MCP streamable-HTTP.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY mcp_servers/oci/requirements.txt ./mcp_servers/oci/requirements.txt
RUN pip install --no-cache-dir -r mcp_servers/oci/requirements.txt

COPY shared ./shared
COPY mcp_servers/oci ./mcp_servers/oci

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8002/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "mcp_servers.oci.server"]
