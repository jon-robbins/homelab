#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "SKIP: docker not available"
  exit 0
fi

if [[ ! -f docker-compose.yml ]]; then
  echo "FAIL: docker-compose.yml not found"
  exit 1
fi

rendered="$(docker compose config 2>/dev/null)" || {
  echo "FAIL: docker compose config failed"
  exit 1
}

fail() {
  echo "FAIL: $1"
  exit 1
}

grep -q "homelab-arr-missing-search:" <<<"$rendered" || fail "homelab-arr-missing-search service missing"

block="$(echo "$rendered" | awk '/^  homelab-arr-missing-search:/{flag=1; next} /^  [a-zA-Z0-9_-]+:/{flag=0} flag')"

grep -q "cap_drop:" <<<"$block" || fail "homelab-arr-missing-search missing cap_drop"
grep -q "ALL" <<<"$block" || fail "homelab-arr-missing-search must cap_drop ALL"
grep -q "homelab_net" <<<"$block" || fail "homelab-arr-missing-search must use homelab_net"
grep -q "ARR_MISSING_SEARCH_ENABLED" <<<"$block" || fail "missing ARR_MISSING_SEARCH_ENABLED env"
default_rendered="$(env -u ARR_MISSING_SEARCH_ENABLED docker compose config 2>/dev/null)" || fail "docker compose config failed without ARR_MISSING_SEARCH_ENABLED"
default_block="$(echo "$default_rendered" | awk '/^  homelab-arr-missing-search:/{flag=1; next} /^  [a-zA-Z0-9_-]+:/{flag=0} flag')"
grep -q 'ARR_MISSING_SEARCH_ENABLED: "0"' <<<"$default_block" || fail "ARR_MISSING_SEARCH_ENABLED must default to 0"
grep -q "sonarr:" <<<"$block" || fail "must depend on sonarr"
grep -q "radarr:" <<<"$block" || fail "must depend on radarr"
healthy_dependencies="$(grep -c "condition: service_healthy" <<<"$block" || true)"
[[ "$healthy_dependencies" -eq 2 ]] || fail "must depend on healthy sonarr and radarr"
grep -q "homelab-arr-missing-search" <<<"$block" || fail "missing homelab-arr-missing-search command"
grep -q -- "--healthcheck" <<<"$block" || fail "missing CLI healthcheck"
grep -q 'user:' <<<"$block" || fail "homelab-arr-missing-search must run as PUID:PGID"

if grep -q "^    ports:" <<<"$block"; then
  fail "homelab-arr-missing-search must not publish host ports"
fi

echo "PASS: homelab-arr-missing-search compose contract"
