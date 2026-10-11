#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "${ROOT}/scripts/ci/export-ci-secret-placeholders.sh"

secret_dir="$(mktemp -d)"
trap 'rm -rf "${secret_dir}"' EXIT
export HOMELAB_SECRET_DIR="${secret_dir}"

while IFS= read -r key; do
  [[ -n "${key}" ]] || continue
  printf 'CI_SENTINEL_%s\n' "${key}" >"${HOMELAB_SECRET_DIR}/${key}"
done < <(managed_secret_keys)

docker compose --env-file /dev/null -f "${ROOT}/docker-compose.yml" config --quiet
