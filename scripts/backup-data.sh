#!/usr/bin/env bash
# Pre-flight backup of ./data to ${MEDIA_HDD_PATH}/backups/homelab-data/
# Run from repo root (or rely on script location). Exits non-zero on failure
# so callers (e.g. nightly deploy) can abort before changing containers.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  source ".env"
  set +a
fi

DEST_DRIVE="${MEDIA_HDD_PATH:-/mnt/media-hdd}"
BACKUP_DIR="${DEST_DRIVE}/backups/homelab-data"
SOURCE_DIR="data"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
BACKUP_FILE="${BACKUP_DIR}/homelab_data_${TIMESTAMP}.tar.gz"
RETENTION_DAYS=14

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "backup-data: ERROR: ${REPO_ROOT}/${SOURCE_DIR} does not exist" >&2
  exit 1
fi

echo "backup-data: archiving ${REPO_ROOT}/${SOURCE_DIR} -> ${BACKUP_FILE}"
echo "backup-data: excluding Ollama model blobs (weights); keeping manifests under data/llm/ollama/models/manifests/"

mkdir -p "${BACKUP_DIR}"

tar -czf "${BACKUP_FILE}" \
  --exclude='*.log' \
  --exclude='*.pid' \
  --exclude='data/llm/ollama/models/blobs' \
  -C "${REPO_ROOT}" \
  "${SOURCE_DIR}"

echo "backup-data: created ${BACKUP_FILE}"

echo "backup-data: pruning archives older than ${RETENTION_DAYS} days in ${BACKUP_DIR}"
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'homelab_data_*.tar.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "backup-data: done."
