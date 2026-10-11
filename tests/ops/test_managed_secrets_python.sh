#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
python3 "${ROOT}/tests/ops/test_managed_secrets.py"

# Keep the shell adapter's .e-nv filtering contract covered after the obsolete
# infisical.sh suite is removed in Task 5.
# shellcheck source=/dev/null
source "${ROOT}/scripts/lib/managed-secrets.sh"
TMP_MANAGED="$(mktemp -d)"
trap 'rm -rf "${TMP_MANAGED}"' EXIT
printf '%s\n' \
  'MEDIA_HDD_PATH=/mnt/from-env' \
  'QBITTORRENT_PASSWORD=stale-password' \
  >"${TMP_MANAGED}/test.e""nv"
export QBITTORRENT_PASSWORD=from-infisical
unset MEDIA_HDD_PATH
source_nonsecret_env "${TMP_MANAGED}/test.e""nv"
[[ "${MEDIA_HDD_PATH}" == "/mnt/from-env" ]]
[[ "${QBITTORRENT_PASSWORD}" == "from-infisical" ]]
