#!/usr/bin/env bash
# Trigger Seerr library/metadata sync jobs (Sonarr, Radarr, Plex, availability).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 "${ROOT}/scripts/lib/infisical_fetch.py" require \
  --paths /arr \
  --keys OVERSEERR_API_KEY \
  --reexec "$0" "$@"

BASE_URL="${OVERSEERR_INTERNAL_URL:-http://127.0.0.1:5055}"

run_job() {
  local job="$1"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    -H "X-Api-Key: ${OVERSEERR_API_KEY}" \
    "${BASE_URL}/api/v1/settings/jobs/${job}/run")"
  if [[ "${code}" != "200" ]]; then
    echo "Failed to run job ${job} (HTTP ${code})" >&2
    return 1
  fi
  echo "Started job: ${job}"
}

for job in radarr-scan sonarr-scan plex-recently-added-scan availability-sync; do
  run_job "${job}"
done
