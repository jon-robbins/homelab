#!/usr/bin/env bash
# Thin wrapper — delegates to infisical_fetch.py run (stdlib REST client).
# Usage: run-with-secrets.sh --paths /qbt --keys K1,K2 -- command [args...]
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

paths_csv=""
keys_csv=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --paths) paths_csv="$2"; shift 2 ;;
    --keys)  keys_csv="$2"; shift 2 ;;
    --)      shift; break ;;
    *) echo "FAIL: unknown argument: $1" >&2; exit 1 ;;
  esac
done

if ((${#@} == 0)); then
  echo "FAIL: run-with-secrets requires a command after --" >&2
  exit 1
fi

exec python3 "${REPO_ROOT}/scripts/lib/infisical_fetch.py" run \
  --paths "$paths_csv" \
  ${keys_csv:+--keys "$keys_csv"} \
  -- "$@"
