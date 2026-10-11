#!/usr/bin/env bash
# Grey-cloud A records for direct :8443 ingress (stream.*, jf.*, share.*).
#
# Do NOT use `cloudflared tunnel route dns` — that creates proxied tunnel CNAME.
# Uses Cloudflare API (CLOUDFLARE_TOKEN) — same token Caddy uses for DNS-01.
#
# Usage:
#   ./scripts/ops/media-dns.sh preflight
#   ./scripts/ops/media-dns.sh status
#   ./scripts/ops/media-dns.sh apply [--yes]
#   ./scripts/ops/media-dns.sh remove [--yes]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

INVOCATION=("$0" "$@")
ZONE_NAME="${ZONE_NAME:-example.homelab.local}"
PUBLIC_IP="${PUBLIC_IP:-192.0.2.10}"
MEDIA_HOSTS="${MEDIA_HOSTS:-stream.example.homelab.local jf.example.homelab.local share.example.homelab.local}"
CLOUDFLARED_CONFIG="${CLOUDFLARED_CONFIG:-${REPO_ROOT}/data/cloudflared/config.yml}"
DRY_RUN=0
ASSUME_YES=0
CMD="${1:-status}"
shift || true

log() { printf '==> %s\n' "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) die "unknown option: $1" ;;
  esac
done

ensure_cf_secrets() {
  python3 "${REPO_ROOT}/scripts/lib/infisical_fetch.py" require \
    --paths /network \
    --keys CLOUDFLARE_TOKEN \
    --reexec "${INVOCATION[@]}"
  command -v jq >/dev/null || die "jq required"
}

cf_api() {
  curl -sf -H "Authorization: Bearer ${CLOUDFLARE_TOKEN}" -H "Content-Type: application/json" "$@"
}

zone_id() {
  cf_api "https://api.cloudflare.com/client/v4/zones?name=${ZONE_NAME}" | jq -r '.result[0].id'
}

preflight_tunnel() {
  [[ -f "${CLOUDFLARED_CONFIG}" ]] || die "missing ${CLOUDFLARED_CONFIG}"
  for host in ${MEDIA_HOSTS}; do
    if grep -q "${host}" "${CLOUDFLARED_CONFIG}" 2>/dev/null; then
      log "WARN: ${host} still in tunnel config — remove after :8443 cutover"
    else
      log "OK: ${host} not in ${CLOUDFLARED_CONFIG}"
    fi
  done
  log "NOTE: do NOT run cloudflared tunnel route dns for media hostnames"
}

dns_status_host() {
  local host="$1" zid recs
  zid="$(zone_id)"
  recs="$(cf_api "https://api.cloudflare.com/client/v4/zones/${zid}/dns_records?name=${host}")"
  echo "${recs}" | jq -r '.result[] | "\(.type) \(.name) -> \(.content) proxied=\(.proxied) id=\(.id)"'
  if [[ "$(echo "${recs}" | jq '.result | length')" -eq 0 ]]; then
    echo "(no records for ${host})"
  fi
}

dns_status() {
  ensure_cf_secrets
  for host in ${MEDIA_HOSTS}; do
    echo "--- ${host} ---"
    dns_status_host "${host}"
  done
}

delete_dns_record() {
  local host="$1" rec_id="$2" rtype="$3"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "[dry-run] would DELETE ${rtype} ${host} id=${rec_id}"
    return
  fi
  local resp
  resp="$(cf_api -X DELETE "https://api.cloudflare.com/client/v4/zones/$(zone_id)/dns_records/${rec_id}")"
  echo "${resp}" | jq -e '.success' >/dev/null || die "delete ${rtype} ${host} failed: $(echo "${resp}" | jq -c '.errors')"
  log "deleted ${rtype} ${host}"
}

upsert_a_record() {
  local host="$1" zid rec_id rtype existing_ip existing_proxied
  zid="$(zone_id)"
  local recs
  recs="$(cf_api "https://api.cloudflare.com/client/v4/zones/${zid}/dns_records?name=${host}")"

  rec_id="$(echo "${recs}" | jq -r '.result[] | select(.type=="A") | .id' | head -1)"
  rtype="$(echo "${recs}" | jq -r '.result[0].type // empty')"
  existing_ip="$(echo "${recs}" | jq -r '.result[] | select(.type=="A") | .content' | head -1)"
  existing_proxied="$(echo "${recs}" | jq -r '.result[] | select(.type=="A") | .proxied' | head -1)"

  local payload
  payload="$(jq -n --arg name "${host}" --arg content "${PUBLIC_IP}" \
    '{type:"A", name:$name, content:$content, proxied:false, ttl:1}')"

  if [[ -n "${rec_id}" && "${existing_ip}" == "${PUBLIC_IP}" && "${existing_proxied}" == "false" ]]; then
    log "OK: grey-cloud A already set for ${host} → ${PUBLIC_IP}"
    return
  fi

  # Tunnel CNAME (or any non-matching record) must be removed before A can exist.
  while IFS=$'\t' read -r rid rtyp; do
    [[ -n "${rid}" && -n "${rtyp}" ]] || continue
    if [[ "${rtyp}" == "A" && -n "${rec_id}" && "${rid}" == "${rec_id}" ]]; then
      continue
    fi
    delete_dns_record "${host}" "${rid}" "${rtyp}"
  done < <(echo "${recs}" | jq -r '.result[] | "\(.id)\t\(.type)"')

  if [[ -n "${rec_id}" ]]; then
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      log "[dry-run] would PATCH A ${host} → ${PUBLIC_IP} (proxied=false)"
      return
    fi
    local resp
    resp="$(cf_api -X PATCH "https://api.cloudflare.com/client/v4/zones/${zid}/dns_records/${rec_id}" \
      -d "${payload}")"
    echo "${resp}" | jq -e '.success' >/dev/null || die "patch A ${host} failed: $(echo "${resp}" | jq -c '.errors')"
    log "updated A ${host} → ${PUBLIC_IP} (DNS only)"
  else
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      log "[dry-run] would POST A ${host} → ${PUBLIC_IP} (proxied=false)"
      return
    fi
    local resp
    resp="$(cf_api -X POST "https://api.cloudflare.com/client/v4/zones/${zid}/dns_records" \
      -d "${payload}")"
    echo "${resp}" | jq -e '.success' >/dev/null || die "create A ${host} failed: $(echo "${resp}" | jq -c '.errors')"
    log "created grey-cloud A ${host} → ${PUBLIC_IP}"
  fi
}

remove_a_record() {
  local host="$1" zid recs
  zid="$(zone_id)"
  recs="$(cf_api "https://api.cloudflare.com/client/v4/zones/${zid}/dns_records?name=${host}")"
  if [[ "$(echo "${recs}" | jq '.result | length')" -eq 0 ]]; then
    log "no DNS records for ${host}"
    return 0
  fi
  while IFS=$'\t' read -r rec_id rtype; do
    [[ -n "${rec_id}" && -n "${rtype}" ]] || continue
    delete_dns_record "${host}" "${rec_id}" "${rtype}"
  done < <(echo "${recs}" | jq -r '.result[] | "\(.id)\t\(.type)"')
}

apply_dns() {
  ensure_cf_secrets
  if [[ "${ASSUME_YES}" -eq 0 && "${DRY_RUN}" -eq 0 ]]; then
    read -r -p "Create/update grey-cloud A records for ${MEDIA_HOSTS}? [y/N] " ans
    [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]] || die "aborted"
  fi
  preflight_tunnel
  for host in ${MEDIA_HOSTS}; do
    upsert_a_record "${host}"
  done
}

remove_dns() {
  ensure_cf_secrets
  if [[ "${ASSUME_YES}" -eq 0 && "${DRY_RUN}" -eq 0 ]]; then
    read -r -p "Remove A records for ${MEDIA_HOSTS}? [y/N] " ans
    [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]] || die "aborted"
  fi
  for host in ${MEDIA_HOSTS}; do
    remove_a_record "${host}"
  done
}

case "${CMD}" in
  preflight) preflight_tunnel ;;
  status) dns_status ;;
  apply) apply_dns ;;
  remove) remove_dns ;;
  -h|--help)
    sed -n '2,11p' "$0" | sed 's/^# \?//'
    ;;
  *) die "unknown command: ${CMD} (preflight|status|apply|remove)" ;;
esac
