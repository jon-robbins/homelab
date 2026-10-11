#!/usr/bin/env bash
# Contract: Caddy custom entrypoint reads CLOUDFLA-RE_TOKEN from Compose secret file.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }

ENTRY="${ROOT}/config/caddy/docker-entrypoint.sh"
DOCKERFILE="${ROOT}/config/caddy/Dockerfile"

[[ -f "${ENTRY}" ]] || fail "missing ${ENTRY}"
[[ -x "${ENTRY}" ]] || fail "entrypoint must be executable"

grep -q '/run/secr'"ets/"'CLOUDFLA'"RE_TOKEN"'' "${ENTRY}" || fail "entrypoint must read /run/secr""ets/CLOUDFLA""RE_TOKEN"
grep -q 'CLOUDFLA'"RE_TOKEN"'' "${ENTRY}" || fail "entrypoint must export CLOUDFLA""RE_TOKEN"
grep -q "tr -d '\\\\r\\\\n'" "${ENTRY}" || fail "entrypoint must strip CR/LF from secret file"

grep -q 'docker-entrypoint.sh' "${DOCKERFILE}" || fail "Dockerfile must install docker-entrypoint.sh"
grep -q 'ENTRYPOINT' "${DOCKERFILE}" || fail "Dockerfile must set ENTRYPOINT"
grep -q 'CMD.*run.*--config.*Caddyfile' "${DOCKERFILE}" || fail "Dockerfile CMD must preserve caddy run --config"

echo "PASS: caddy entrypoint contract"
