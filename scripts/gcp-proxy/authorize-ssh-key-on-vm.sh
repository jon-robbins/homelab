#!/bin/bash
# Append ~/.ssh/id_ed25519.pub to an existing Compute Engine VM so the homelab can run
# gcp-proxy-tunnel (SSH → tinyproxy on the instance). Safe merge: does not remove other keys.
#
# Prereq: gcloud auth (same as provision-gcp-vm.sh).
#
# Usage:
#   export GCP_PROJECT="homelab-474311"
#   export GCP_ZONE="us-central1-f"
#   export GCP_INSTANCE_NAME="flaresolverr-vpn"   # optional
#   ./gcp/authorize-ssh-key-on-vm.sh
#
# Optional:
#   export GCP_SSH_USER="ubuntu"
#   export GCP_SSH_PUB_KEY_FILE="$HOME/.ssh/id_ed25519.pub"

set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT}"
GCP_ZONE="${GCP_ZONE:-us-central1-f}"
GCP_INSTANCE_NAME="${GCP_INSTANCE_NAME:-flaresolverr-vpn}"
GCP_SSH_USER="${GCP_SSH_USER:-ubuntu}"
GCP_SSH_PUB_KEY_FILE="${GCP_SSH_PUB_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}"

if [[ ! -f "$GCP_SSH_PUB_KEY_FILE" ]]; then
  echo "Missing public key: $GCP_SSH_PUB_KEY_FILE" >&2
  exit 1
fi

PUB_LINE=$(tr -d '\n' <"$GCP_SSH_PUB_KEY_FILE")

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
IMAGE="google/cloud-sdk:slim"

docker pull "$IMAGE" >/dev/null

TTY_ARGS=()
if [[ -t 0 ]] && [[ -t 1 ]]; then
  TTY_ARGS=(-it)
else
  TTY_ARGS=(-i)
fi

exec docker run --rm "${TTY_ARGS[@]}" \
  -e "GCP_PROJECT=$GCP_PROJECT" \
  -e "GCP_ZONE=$GCP_ZONE" \
  -e "GCP_INSTANCE_NAME=$GCP_INSTANCE_NAME" \
  -e "GCP_SSH_USER=$GCP_SSH_USER" \
  -e "PUB_LINE=$PUB_LINE" \
  -v "$HOME/.config/gcloud:/root/.config/gcloud" \
  -v "$SCRIPT_DIR:/gcp:ro" \
  -w /gcp \
  "$IMAGE" \
  bash -lc 'exec bash /gcp/authorize-ssh-key-on-vm-inner.sh'
