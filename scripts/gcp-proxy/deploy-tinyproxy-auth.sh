#!/bin/bash
# Build and run the BasicAuth tinyproxy container on the GCP VM (over SSH).
# Set credentials in .env next to docker-compose.yml (same values as Prowlarr proxy user/pass):
#
#   GCP_PROXY_IP=35.225.24.16
#   TINYPROXY_USER=prowlarr
#   TINYPROXY_PASSWORD=your-long-secret-here
#
# Usage (from repo root):
#   set -a && source .env && set +a && ./gcp/deploy-tinyproxy-auth.sh

set -euo pipefail

: "${GCP_PROXY_IP:?Set GCP_PROXY_IP (e.g. in .env)}"
: "${TINYPROXY_USER:?Set TINYPROXY_USER}"
: "${TINYPROXY_PASSWORD:?Set TINYPROXY_PASSWORD}"

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
USER="${GCP_PROXY_USER:-ubuntu}"
REMOTE_DIR=/tmp/tinyproxy-auth-$$

ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  "${USER}@${GCP_PROXY_IP}" "mkdir -p ${REMOTE_DIR}"
scp -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -r \
  "$ROOT/gcp/tinyproxy-auth/"* "${USER}@${GCP_PROXY_IP}:${REMOTE_DIR}/"

ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  "${USER}@${GCP_PROXY_IP}" bash -s <<EOF
set -euo pipefail
cd ${REMOTE_DIR}
sudo docker build -t tinyproxy-auth:local .
sudo docker rm -f tinyproxy 2>/dev/null || true
sudo docker run -d --name tinyproxy --restart unless-stopped \\
  -e TINYPROXY_USER=${TINYPROXY_USER@Q} \\
  -e TINYPROXY_PASSWORD=${TINYPROXY_PASSWORD@Q} \\
  -p 127.0.0.1:8888:8888 \\
  tinyproxy-auth:local
rm -rf ${REMOTE_DIR}
sudo ss -tlnp | grep 8888 || true
EOF

echo ""
echo "Prowlarr → Settings → General → Proxy: host 127.0.0.1 port 8888"
echo "  Username: ${TINYPROXY_USER}"
echo "  Password: (same as TINYPROXY_PASSWORD in .env)"
