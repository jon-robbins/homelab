#!/bin/sh
# slskd reads its settings from the environment but not from files, so export the mounted secrets and hand over
# to the image's own entrypoint, which drops to PUID:PGID. slskd refuses to start without its download
# directories, so create them (as the service user) first.
set -eu

mkdir -p "$SLSKD_DOWNLOADS_DIR" "$SLSKD_INCOMPLETE_DIR"
chown "$PUID:$PGID" "$SLSKD_DOWNLOADS_DIR" "$SLSKD_INCOMPLETE_DIR"

SLSKD_SLSK_USERNAME=$(cat /run/secrets/SLSKD_SLSK_USERNAME)
SLSKD_SLSK_PASSWORD=$(cat /run/secrets/SLSKD_SLSK_PASSWORD)
SLSKD_PASSWORD=$(cat /run/secrets/SLSKD_WEB_PASSWORD)
SLSKD_API_KEY=$(cat /run/secrets/SLSKD_API_KEY)
export SLSKD_SLSK_USERNAME SLSKD_SLSK_PASSWORD SLSKD_PASSWORD SLSKD_API_KEY

exec /entrypoint.sh "$@"
