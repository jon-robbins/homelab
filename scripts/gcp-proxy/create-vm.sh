#!/bin/bash
# Create a small GCP VM with tinyproxy (loopback-only) for Prowlarr egress.
# Prereqs: gcloud installed, `gcloud auth login`, billing enabled, Compute Engine API enabled.
#
# Usage:
#   export GCP_PROJECT="your-project-id"
#   export GCP_ZONE="europe-southwest1-a"    # Madrid; change if you prefer
#   export GCP_INSTANCE_NAME="prowlarr-proxy"
#   ./create-vm.sh
#
# Optional:
#   export GCP_SSH_KEY_FILE="$HOME/.ssh/id_ed25519.pub"
#   export GCP_MACHINE_TYPE="e2-micro"

set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT to your gcloud project ID}"
GCP_ZONE="${GCP_ZONE:-europe-southwest1-a}"
GCP_INSTANCE_NAME="${GCP_INSTANCE_NAME:-prowlarr-proxy}"
GCP_MACHINE_TYPE="${GCP_MACHINE_TYPE:-e2-micro}"
GCP_SSH_KEY_FILE="${GCP_SSH_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}"

if [[ ! -f "$GCP_SSH_KEY_FILE" ]]; then
  echo "SSH public key not found: $GCP_SSH_KEY_FILE" >&2
  echo "Generate with: ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519" >&2
  exit 1
fi

KEY_CONTENT=$(cat "$GCP_SSH_KEY_FILE")
SSH_KEYS_METADATA="ubuntu:${KEY_CONTENT}"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "Enabling compute.googleapis.com (idempotent)..."
gcloud services enable compute.googleapis.com --project="$GCP_PROJECT" --quiet

echo "Creating firewall rule: allow SSH to instances tagged ${GCP_INSTANCE_NAME}-ssh..."
if ! gcloud compute firewall-rules describe "${GCP_INSTANCE_NAME}-ssh" --project="$GCP_PROJECT" &>/dev/null; then
  gcloud compute firewall-rules create "${GCP_INSTANCE_NAME}-ssh" \
    --project="$GCP_PROJECT" \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:22 \
    --source-ranges=0.0.0.0/0 \
    --target-tags="${GCP_INSTANCE_NAME}-ssh" \
    --quiet
else
  echo "Firewall rule ${GCP_INSTANCE_NAME}-ssh already exists, skipping."
fi

echo "Creating VM ${GCP_INSTANCE_NAME}..."
if gcloud compute instances describe "$GCP_INSTANCE_NAME" --zone="$GCP_ZONE" --project="$GCP_PROJECT" &>/dev/null; then
  echo "Instance $GCP_INSTANCE_NAME already exists. Not overwriting."
else
  gcloud compute instances create "$GCP_INSTANCE_NAME" \
    --project="$GCP_PROJECT" \
    --zone="$GCP_ZONE" \
    --machine-type="$GCP_MACHINE_TYPE" \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=10GB \
    --boot-disk-type=pd-balanced \
    --tags="${GCP_INSTANCE_NAME}-ssh" \
    --metadata-from-file=startup-script="$SCRIPT_DIR/startup-tinyproxy.sh" \
    --metadata=ssh-keys="$SSH_KEYS_METADATA" \
    --quiet
fi

EXTERNAL_IP=$(gcloud compute instances describe "$GCP_INSTANCE_NAME" \
  --zone="$GCP_ZONE" --project="$GCP_PROJECT" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo ""
echo "=== Done ==="
echo "External IP: $EXTERNAL_IP"
echo ""
echo "1) On this machine, trust host key and verify tinyproxy on the VM:"
echo "   ssh -o StrictHostKeyChecking=accept-new ubuntu@$EXTERNAL_IP 'sudo ss -tlnp | grep 8888 || true'"
echo ""
echo "2) Install systemd: see gcp/home-systemd/README.txt"
echo "3) Prowlarr → Settings → General → Proxy: host 127.0.0.1 port 8888 (HTTP; empty user/pass)"
echo ""
echo "Optional: tighten firewall rule ${GCP_INSTANCE_NAME}-ssh to your home IP only (GCP Console → VPC → Firewall)."
