#!/usr/bin/env bash
# Contract: Seerr custom entrypoint + compose wiring for TMDB key + imageproxy fix.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }

ENTRY="${ROOT}/config/seerr/entrypoint.sh"
COMPOSE="${ROOT}/docker-compose.yml"
ARR="${ROOT}/compose/arr.yml"

[[ -f "${ENTRY}" ]] || fail "missing ${ENTRY}"
[[ -x "${ENTRY}" ]] || fail "entrypoint must be executable"

grep -q 'TMDB_API_KEY' "${ENTRY}" || fail "entrypoint must patch TMDB_API_KEY"
grep -q '/run/secr'"ets/"'TMDB_API_KEY' "${ENTRY}" || fail "entrypoint must read /run/secr""ets/TMDB_API_KEY"
grep -q 'imageproxy.js' "${ENTRY}" || fail "entrypoint must patch imageproxy.js"
grep -q 'requestInterceptorFunction' "${ENTRY}" || fail "entrypoint must replace requestInterceptorFunction"
grep -q '(config) => config' "${ENTRY}" || fail "entrypoint must install no-op axios interceptor"

grep -q 'overseerr:' "${ARR}" || fail "overseerr service missing from arr.yml"
rendered="$(docker compose -f "${COMPOSE}" config 2>/dev/null)" || fail "compose config failed for docker-compose.yml"
grep -q 'overseerr:' <<<"${rendered}" || fail "overseerr service missing"
block="$(echo "${rendered}" | awk '/^  overseerr:/{flag=1; next} /^  [a-zA-Z0-9_-]+:/{flag=0} flag')"
grep -q 'custom-entrypoint' <<<"${block}" || fail "overseerr must use custom entrypoint"
grep -q '/run/secr'"ets/"'TMDB_API_KEY' <<<"${block}" || fail "overseerr must grant TMDB_API_KEY secret mount"
grep -q 'config/seerr/entrypoint.sh' <<<"${block}" || fail "overseerr must mount seerr entrypoint"
grep -q 'TMDB_API_KEY=' <<<"${block}" && fail "overseerr must not pass TMDB_API_KEY via environment"

echo "PASS: seerr entrypoint / compose contract"
