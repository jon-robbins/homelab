#!/usr/bin/env bash
# Relocate Sonarr/Radarr imports from NVMe staging to HDD library paths in qBittorrent.
# Runs inside homelab-workers (homelab_net + host media mounts) so arr/qBT APIs are reachable.
# Suggested crontab: */5 * * * * ${HOMELAB_ROOT}/scripts/ops/qbt-arr-post-import.sh >> ${HOMELAB_ROOT}/logs/qbt-arr-post-import.log 2>&1
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

CONTAINER="${HOMELAB_WORKERS_CONTAINER:-homelab-homelab-workers-1}"

if docker inspect "${CONTAINER}" >/dev/null 2>&1; then
  exec docker exec "${CONTAINER}" qbt-arr-post-import "$@"
fi

# Fallback: one-shot container on homelab_net when homelab-workers is not running.
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/lib/managed-secrets.sh"

python3 "${REPO_ROOT}/scripts/lib/infisical_fetch.py" require \
  --paths /arr,/qbt \
  --keys SONARR_API_KEY,RADARR_API_KEY,QBITTORRENT_USERNAME,QBITTORRENT_PASSWORD \
  --reexec "$0" "$@"

source_nonsecret_env "${REPO_ROOT}/.env"

MEDIA_NVME="${MEDIA_NVME_PATH:-/mnt/media-nvme}"
MEDIA_HDD="${MEDIA_HDD_PATH:-/mnt/media-hdd}"
IMAGE="${HOMELAB_WORKERS_IMAGE:-local/homelab-workers:latest}"

exec docker run --rm --network homelab_net --user "${PUID:-1000}:${PGID:-1000}" \
  -v "${REPO_ROOT}/data/homelab-workers:/data" \
  -v "${MEDIA_NVME}:${MEDIA_NVME}" \
  -v "${MEDIA_HDD}:${MEDIA_HDD}" \
  -e SONARR_INTERNAL_URL="${SONARR_INTERNAL_URL:-http://sonarr:8989/sonarr}" \
  -e RADARR_INTERNAL_URL="${RADARR_INTERNAL_URL:-http://radarr:7878/radarr}" \
  -e SONARR_API_KEY \
  -e RADARR_API_KEY \
  -e QBITTORRENT_INTERNAL_URL="${QBITTORRENT_INTERNAL_URL:-http://qbittorrent:8080}" \
  -e QBITTORRENT_USERNAME \
  -e QBITTORRENT_PASSWORD \
  -e ARR_POST_IMPORT_STATE_PATH="${ARR_POST_IMPORT_STATE_PATH:-/data/arr-post-import-state.json}" \
  -e MEDIA_NVME_PATH="${MEDIA_NVME}" \
  -e MEDIA_HDD_PATH="${MEDIA_HDD}" \
  --entrypoint qbt-arr-post-import \
  "${IMAGE}" "$@"
