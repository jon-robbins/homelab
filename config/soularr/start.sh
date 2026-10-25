#!/bin/bash
# Soularr reads only config.ini, so render it from the mounted secrets into /tmp (container-local, never on a host
# disk), then start the image's polling loop with that config and /data for its state. Search preferences come
# from SOULARR_* variables a site override may set; the defaults are Soularr's own.
set -euo pipefail

umask 077
mkdir -p /tmp/soularr
cat >/tmp/soularr/config.ini <<EOF
[Lidarr]
api_key = $(cat /run/secrets/LIDARR_API_KEY)
host_url = http://lidarr:8686/lidarr
download_dir = /downloads/soulseek

[Slskd]
api_key = $(cat /run/secrets/SLSKD_API_KEY)
host_url = http://slskd:5030
url_base = /slskd
download_dir = /downloads/soulseek

[Release Settings]
skip_region_check = ${SOULARR_SKIP_REGION_CHECK:-False}

[Search Settings]
allowed_filetypes = ${SOULARR_ALLOWED_FILETYPES:-flac,mp3}
number_of_albums_to_grab = ${SOULARR_ALBUMS_PER_RUN:-10}
minimum_filename_match_ratio = ${SOULARR_MIN_MATCH_RATIO:-0.5}
search_retry_wait_hours = ${SOULARR_SEARCH_RETRY_WAIT_HOURS:-0}

[Logging]
log_to_file = False
EOF

exec /app/run.sh --config-dir /tmp/soularr --var-dir /data
