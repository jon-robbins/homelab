#!/usr/bin/env bash
# Report managed secret keys that still have nonempty values in a local .env file.
# Prints key names only — never values.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/lib/managed-secrets.sh"

ENV_FILE="${1:-${HOMELAB_ENV_FILE:-${HOME}/homelab/.env}}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "OK: no .env file at ${ENV_FILE}"
  exit 0
fi

trim_whitespace() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

strip_quotes() {
  local s="$1"
  if [[ "$s" == \"*\" && "$s" == *\" ]]; then
    s="${s:1:${#s}-2}"
  elif [[ "$s" == \'*\' && "$s" == *\' ]]; then
    s="${s:1:${#s}-2}"
  fi
  printf '%s' "$s"
}

value_nonempty() {
  local val
  val="$(trim_whitespace "$1")"
  val="$(strip_quotes "$val")"
  val="$(trim_whitespace "$val")"
  [[ -n "$val" ]]
}

found=()
while IFS= read -r key; do
  [[ -n "$key" ]] || continue
  line="$(grep -E "^(export[[:space:]]+)?${key}=" "$ENV_FILE" 2>/dev/null | tail -1 || true)"
  [[ -n "$line" ]] || continue
  line="${line#export}"
  line="$(trim_whitespace "$line")"
  val="${line#*=}"
  if value_nonempty "$val"; then
    found+=("$key")
  fi
done < <(managed_secret_keys)

if ((${#found[@]} == 0)); then
  echo "OK: no managed secret values in ${ENV_FILE}"
  exit 0
fi

echo "FAIL: local .env still contains values for:"
for key in "${found[@]}"; do
  echo "  ${key}"
done
exit 1
