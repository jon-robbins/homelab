#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

mapfile -t services < <(docker compose config --services 2>/dev/null | sort)
required=(autobrr bazarr qbittorrent)
fail=0
for svc in "${required[@]}"; do
  if ! printf '%s\n' "${services[@]}" | grep -qx "$svc"; then
    echo "FAIL: compose missing service $svc"
    fail=1
  fi
  if ! grep -q "$svc" docs/network-access.md; then
    echo "FAIL: docs/network-access.md missing row for $svc"
    fail=1
  fi
done
if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "PASS: operator services documented"
