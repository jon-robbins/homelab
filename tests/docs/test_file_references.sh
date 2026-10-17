#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
shopt -s globstar nullglob

fail=0
check_link() {
  local src="$1" target="$2"
  local resolved
  if [[ "$target" == /* ]]; then
    resolved="$target"
  else
    resolved="$(dirname "$src")/$target"
  fi
  resolved="$(python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$resolved")"
  if [[ ! -e "$resolved" ]]; then
    echo "FAIL: $src -> $target (missing)"
    fail=1
  fi
}

# Scope-B docs
DOCS=(
  README.md
  docs/network-access.md
  docs/ingress.md
  docs/caddy-guide.md
  docs/service-troubleshooting.md
  docs/modules.md
  docs/plugins.md
  docs/hardware.md
  docs/architecture.md
  docs/configuration.md
  docs/operations.md
  docs/development.md
)

  re='\]\(([^)#]+)\)'
  for doc in "${DOCS[@]}"; do
  [[ -f "$doc" ]] || { echo "FAIL: missing doc $doc"; fail=1; continue; }
  while IFS= read -r line; do
    if [[ "$line" =~ $re ]]; then
      rel="${BASH_REMATCH[1]}"
      [[ "$rel" =~ ^https?:// ]] && continue
      [[ "$rel" =~ ^# ]] && continue
      check_link "$doc" "$rel"
    fi
  done < "$doc"
done

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "PASS: file references"
