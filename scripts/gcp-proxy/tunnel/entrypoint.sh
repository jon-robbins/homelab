#!/bin/bash
set -euo pipefail

if [[ -z "${GCP_PROXY_IP:-}" ]]; then
  echo "gcp-proxy-tunnel: set GCP_PROXY_IP (e.g. in .env next to docker-compose)." >&2
  exit 1
fi

GCP_PROXY_USER="${GCP_PROXY_USER:-ubuntu}"
KEY="${SSH_PRIVATE_KEY:-/ssh/id_ed25519}"

if [[ ! -f "$KEY" ]]; then
  echo "gcp-proxy-tunnel: missing SSH private key at $KEY (mount with compose volumes)." >&2
  exit 1
fi

mkdir -p /root/.ssh
chmod 700 /root/.ssh
install -m 600 "$KEY" /root/.ssh/id_ed25519

# Avoid interactive prompts on first connect
ssh-keyscan -H "$GCP_PROXY_IP" >>/root/.ssh/known_hosts 2>/dev/null || true

exec ssh -N \
  -i /root/.ssh/id_ed25519 \
  -o BatchMode=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=60 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=accept-new \
  -L 127.0.0.1:8888:127.0.0.1:8888 \
  "${GCP_PROXY_USER}@${GCP_PROXY_IP}"
