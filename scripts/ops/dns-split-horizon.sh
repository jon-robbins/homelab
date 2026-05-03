#!/usr/bin/env bash
# Split-horizon DNS migration: remove LAN IPs from Cloudflare public DNS
# and add them as Pi-hole local DNS entries instead.
#
# Prerequisites:
#   - CLOUDFLARE_TOKEN in .env (scoped to the zone)
#   - Pi-hole running and reachable at PIHOLE_URL (defaults to http://127.0.0.1:8083)
#   - jq installed
#
# Usage: bash scripts/ops/dns-split-horizon.sh [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1 && info "Dry-run mode — no changes will be made"

# Load CLOUDFLARE_TOKEN from .env
if [[ -f "${REPO_ROOT}/.env" ]]; then
  CLOUDFLARE_TOKEN="$(awk -F= '/^CLOUDFLARE_TOKEN=/ {print substr($0, index($0,"=")+1); exit}' "${REPO_ROOT}/.env")"
fi
: "${CLOUDFLARE_TOKEN:?Set CLOUDFLARE_TOKEN in .env or environment}"

command -v jq >/dev/null 2>&1 || { error "jq is required but not installed"; exit 1; }

ZONE_NAME="ashorkqueen.xyz"
LAN_IP="192.168.1.184"
PIHOLE_CUSTOM_DNS="${REPO_ROOT}/data/pihole/etc-pihole/custom.list"

# Hostnames that should resolve locally, not publicly.
HOSTNAMES=(
  "home.${ZONE_NAME}"
  "plex.home.${ZONE_NAME}"
  "jellyfin.home.${ZONE_NAME}"
  "pihole.home.${ZONE_NAME}"
  "openclaw.home.${ZONE_NAME}"
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

# ── Step 3: Add Pi-hole local DNS entries ─────────────────────────────
info "Updating Pi-hole custom DNS at ${PIHOLE_CUSTOM_DNS}"
mkdir -p "$(dirname "$PIHOLE_CUSTOM_DNS")"
touch "$PIHOLE_CUSTOM_DNS"

CHANGED=0
for host in "${HOSTNAMES[@]}"; do
  ENTRY="${LAN_IP} ${host}"
  if grep -qF "$host" "$PIHOLE_CUSTOM_DNS" 2>/dev/null; then
    info "  Already present: ${host}"
  else
    if [[ "$DRY_RUN" -eq 1 ]]; then
      warn "  Would add: ${ENTRY}"
    else
      echo "$ENTRY" >> "$PIHOLE_CUSTOM_DNS"
      info "  Added: ${ENTRY}"
      CHANGED=1
    fi
  fi
done

if [[ "$CHANGED" -eq 1 ]]; then
  info "Restart Pi-hole DNS to pick up changes:"
  info "  docker exec pihole pihole restartdns"
fi

info "Done. Verify with: dig @127.0.0.1 home.${ZONE_NAME}"
