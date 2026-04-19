#!/bin/bash
# Provision the GCP VM using the official Cloud SDK Docker image (no local gcloud install).
#
# One-time auth (host browser):
#   docker run --rm -it -v "$HOME/.config/gcloud:/root/.config/gcloud" google/cloud-sdk:slim gcloud auth login
#
# Usage:
#   export GCP_PROJECT="your-project-id"
#   export GCP_ZONE="${GCP_ZONE:-europe-southwest1-a}"
#   ./gcp/provision-gcp-vm.sh

set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT}"

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ZONE="${GCP_ZONE:-europe-southwest1-a}"
NAME="${GCP_INSTANCE_NAME:-prowlarr-proxy}"
IMAGE="google/cloud-sdk:slim"
KEY_PRIV="${HOME}/.ssh/id_ed25519"
KEY_PUB="${HOME}/.ssh/id_ed25519.pub"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
if [[ ! -f "$KEY_PRIV" ]]; then
  echo "Generating SSH key for GCP: $KEY_PRIV"
  ssh-keygen -t ed25519 -f "$KEY_PRIV" -N "" -C "prowlarr-gcp-proxy"
fi

docker pull "$IMAGE"

TTY_ARGS=()
if [[ -t 0 ]] && [[ -t 1 ]]; then
  TTY_ARGS=(-it)
else
  TTY_ARGS=(-i)
fi

exec docker run --rm "${TTY_ARGS[@]}" \
  -e "GCP_PROJECT=$GCP_PROJECT" \
  -e "GCP_ZONE=$ZONE" \
  -e "GCP_INSTANCE_NAME=$NAME" \
  -e "GCP_SSH_KEY_FILE=/tmp/prowlarr_gcp.pub" \
  -v "$KEY_PUB:/tmp/prowlarr_gcp.pub:ro" \
  -v "$HOME/.config/gcloud:/root/.config/gcloud" \
  -v "$ROOT/gcp:/gcp" \
  -w /gcp \
  "$IMAGE" \
  bash -lc 'set -euo pipefail; chmod +x /gcp/create-vm.sh && exec /gcp/create-vm.sh'
