#!/usr/bin/env bash
# Point qBittorrent completion hook at dispatcher.sh (dry-run by default).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/lib/managed-secrets.sh"

APPLY=0
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=1
  shift
fi

PREFS='{"autorun_enabled":true,"autorun_program":"/scripts/dispatcher.sh \"%I\""}'

if [[ "$APPLY" -eq 0 ]]; then
  source_nonsecret_env "${REPO_ROOT}/.env"
  QBT_URL="${QBITTORRENT_INTERNAL_URL:-http://qbittorrent:8080}"
  echo "dry-run: would POST ${QBT_URL}/api/v2/app/setPreferences"
  echo "  json=${PREFS}"
  echo "Re-run with --apply to apply (requires QBITTORRENT_USERNAME/PASSWORD via Infisical or env)."
  exit 0
fi

python3 "${REPO_ROOT}/scripts/lib/infisical_fetch.py" require \
  --paths /qbt \
  --keys QBITTORRENT_USERNAME,QBITTORRENT_PASSWORD \
  --reexec "$0" --apply "$@"

source_nonsecret_env "${REPO_ROOT}/.env"

QBT_URL="${QBITTORRENT_INTERNAL_URL:-http://qbittorrent:8080}"
QBT_USER="${QBITTORRENT_USERNAME:-}"
QBT_PASS="${QBITTORRENT_PASSWORD:-}"

if [[ -z "$QBT_USER" || -z "$QBT_PASS" ]]; then
  echo "ERROR: QBITTORRENT_USERNAME and QBITTORRENT_PASSWORD required for --apply" >&2
  exit 1
fi

cookie_jar=$(mktemp)
trap 'rm -f "$cookie_jar"' EXIT

curl -sf -c "$cookie_jar" -b "$cookie_jar" \
  --data-urlencode "username=${QBT_USER}" \
  --data-urlencode "password=${QBT_PASS}" \
  "${QBT_URL}/api/v2/auth/login" >/dev/null

curl -sf -c "$cookie_jar" -b "$cookie_jar" \
  --data-urlencode "json=${PREFS}" \
  "${QBT_URL}/api/v2/app/setPreferences"

echo "qBT autorun updated to dispatcher.sh"
