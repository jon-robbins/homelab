# Build context is apps/ so the shared homelab-workers package installs alongside.
FROM python:3.15-alpine@sha256:2699b90f4d562a22e2dd076c82f964032f64814204bff36d271a54ba4cc2c7e2

WORKDIR /app

COPY homelab-workers /app/homelab-workers
COPY homelab-mcp /app/homelab-mcp

RUN pip install --no-cache-dir /app/homelab-workers /app/homelab-mcp \
    && rm -rf /app/homelab-workers /app/homelab-mcp

ENV HOMELAB_MCP_HOST=0.0.0.0 HOMELAB_MCP_PORT=8090

EXPOSE 8090

ENTRYPOINT ["homelab-mcp"]
