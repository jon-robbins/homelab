#!/bin/sh
# qBittorrent "Run external program on torrent completion" — pass %I as hash
HASH="$1"
[ -n "$HASH" ] || exit 0
wget -qO- --post-data="{\"hash\":\"$HASH\"}" \
  --header="Content-Type: application/json" \
  "http://homelab-qbt-offload:8099/v1/offload" || true
