#!/usr/bin/env bash
# Build local Seerr image from PR #1918 (books / Readarr preview).
# See scripts/README.md for data-dir clone and compose up steps.
set -euo pipefail

SEERR_REPO_URL="${SEERR_REPO_URL:-https://github.com/seerr-team/seerr.git}"
SEERR_PR_REF="${SEERR_PR_REF:-pull/1918/head}"
IMAGE_TAG="${SEERR_PR1918_IMAGE:-seerr:pr1918}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR="${SEERR_PR1918_CLONE_DIR:-$ROOT/../seerr-pr1918-build}"

mkdir -p "$(dirname "$WORKDIR")"
if [[ ! -d "$WORKDIR/.git" ]]; then
  git clone "$SEERR_REPO_URL" "$WORKDIR"
fi
cd "$WORKDIR"
git fetch origin "${SEERR_PR_REF}:refs/heads/seerr-pr1918"
git checkout seerr-pr1918

echo "Building ${IMAGE_TAG} from ${WORKDIR} (ref ${SEERR_PR_REF})..."
docker build -t "${IMAGE_TAG}" .

echo "Done. Next: clone config if needed, then:"
echo "  docker compose -f ${ROOT}/docker-compose.media.yml up -d overseerr-pr1918"
