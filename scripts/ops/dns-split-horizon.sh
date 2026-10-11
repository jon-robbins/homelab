#!/usr/bin/env bash
# Split-horizon DNS: remove LAN IPs from Cloudflare public DNS.
# Local LAN resolution is operator-managed (Pi-hole is retired from this stack).
#
# Prerequisites:
#   - CLOUDFLARE_TOKEN via Infisical or environment
#   - jq installed
#
# Usage: bash scripts/ops/dns-split-horizon.sh [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

python3 "${REPO_ROOT}/scripts/lib/infisical_fetch.py" require \
  --paths /network \
  --keys CLOUDFLARE_TOKEN \
  --reexec "$0" "$@"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1 && info "Dry-run mode — no changes will be made"

command -v jq >/dev/null 2>&1 || { error "jq is required but not installed"; exit 1; }

ZONE_NAME="${ZONE_NAME:-example.homelab.local}"
LAN_IP="${LAN_IP:-192.0.2.184}"

# Hostnames that should not resolve publicly to the LAN IP.
HOSTNAMES=(
  "home.${ZONE_NAME}"
  "plex.home.${ZONE_NAME}"
  "jellyfin.home.${ZONE_NAME}"
)

cf_api() {
  curl -sf -H "Authorization: Bearer ${CLOUDFLARE_TOKEN}" -H "Content-Type: application/json" "$@"
}

# ── Step 1: Get zone ID ──────────────────────────────────────────────
info "Looking up zone ID for ${ZONE_NAME}"
ZONE_ID="$(cf_api "https://api.cloudflare.com/client/v4/zones?name=${ZONE_NAME}" | jq -r '.result[0].id')"
if [[ -z "$ZONE_ID" || "$ZONE_ID" == "null" ]]; then
  error "Could not find zone ID for ${ZONE_NAME}"
  exit 1
fi
info "Zone ID: ${ZONE_ID}"

# ── Step 2: Delete A records pointing to LAN IPs ─────────────────────
for host in "${HOSTNAMES[@]}"; do
  info "Checking DNS records for ${host}"
  RECORDS="$(cf_api "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records?name=${host}&type=A")"
  RECORD_IDS="$(echo "$RECORDS" | jq -r '.result[] | select(.content == "'"${LAN_IP}"'") | .id')"

  if [[ -z "$RECORD_IDS" ]]; then
    info "  No A record pointing to ${LAN_IP} found for ${host}"
    continue
  fi

  while IFS= read -r rid; do
    if [[ "$DRY_RUN" -eq 1 ]]; then
      warn "  Would delete record ${rid} (${host} -> ${LAN_IP})"
    else
      cf_api -X DELETE "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records/${rid}" >/dev/null
      info "  Deleted record ${rid} (${host} -> ${LAN_IP})"
    fi
  done <<< "$RECORD_IDS"
done

info "Done. Public LAN A records cleared for listed hostnames."
info "Configure LAN-local DNS on the operator DNS server if clients still need local resolution."
info "Verify public side with: dig @1.1.1.1 home.${ZONE_NAME}"
