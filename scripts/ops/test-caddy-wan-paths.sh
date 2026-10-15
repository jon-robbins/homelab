#!/usr/bin/env bash
# scripts/ops/test-caddy-wan-paths.sh — verify Caddy gates sensitive home.* paths on direct WAN SNI
set -euo pipefail

PUBLIC_IP="${PUBLIC_IP:-192.0.2.10}"
HOST="home.example.homelab.local"
resolve=(--resolve "${HOST}:443:${PUBLIC_IP}")

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

check() {
  local path="$1" expected_regex="$2" label="$3"
  local code
  code="$(curl -sk -o /dev/null -w '%{http_code}' "${resolve[@]}" "https://${HOST}${path}")"
  if [[ "${code}" =~ ${expected_regex} ]]; then
    pass "${label} → HTTP ${code}"
  else
    fail "${label} → HTTP ${code} (expected ${expected_regex})"
  fi
}

echo "==> Caddy WAN path checks via direct SNI to ${PUBLIC_IP}:443"
echo "    (simulates WAN client hitting home.* on :443 / :8443, not Cloudflare Tunnel)"

# Catch-all for non-internal: 403
check "/" '^(200|301|302|403)$' "GET / (root)"

# @internal_only paths must NOT return 200 to WAN
check "/sonarr" '^(403|401)$' "GET /sonarr (must be blocked)"
check "/radarr" '^(403|401)$' "GET /radarr (must be blocked)"
check "/qbittorrent/" '^(403|401)$' "GET /qbittorrent/ (must be blocked)"
check "/autobrr/" '^(403|401)$' "GET /autobrr/ (must be blocked)"
check "/autobrr" '^(301|302|403|401)$' "GET /autobrr (redirect or blocked)"

echo "OK: Caddy WAN path preflight complete"
