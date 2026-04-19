#!/bin/bash
# GCP instance startup: Docker tinyproxy on loopback only (SSH tunnel from home hits 127.0.0.1:8888).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

docker rm -f tinyproxy 2>/dev/null || true
docker pull vimagick/tinyproxy:latest
docker run -d --name tinyproxy --restart unless-stopped \
  -p 127.0.0.1:8888:8888 \
  vimagick/tinyproxy:latest
# For BasicAuth + stronger defaults, run from your homelab: ./gcp/deploy-tinyproxy-auth.sh (see gcp/README.txt).
