FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*

COPY apps/homelab-workers /app/homelab-workers
COPY apps/homelab-mcp /app/homelab-mcp

# Host umask 077 can leave COPY'd sources root-owned mode 600; the service runs as PUID.
RUN chmod -R a+rX /app/homelab-mcp /app/homelab-workers

RUN pip install --no-cache-dir -e /app/homelab-workers -e /app/homelab-mcp

ENV HOMELAB_MCP_HOST=0.0.0.0 HOMELAB_MCP_PORT=8090

EXPOSE 8090

CMD ["homelab-mcp"]
