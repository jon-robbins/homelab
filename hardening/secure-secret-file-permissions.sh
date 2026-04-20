#!/usr/bin/env bash
# Tighten permissions on typical secret-bearing paths under ~/docker (no key rotation).
# Run as the user that owns the files; use sudo for root-owned paths (e.g. configs written by containers).
set -uo pipefail
ROOT="${HOME}/docker"

paths=(
  "${ROOT}/.env"
  "${ROOT}/data/overseerr/settings.json"
  "${ROOT}/data/prowlarr/config.xml"
  "${ROOT}/data/readarr/config.xml"
  "${ROOT}/data/sonarr/config.xml"
  "${ROOT}/data/radarr/config.xml"
  "${ROOT}/data/jackett/Jackett/ServerConfig.json"
  "${ROOT}/data/qbittorrent/config/qBittorrent/qBittorrent.conf"
)

for p in "${paths[@]}"; do
  if [[ -e "$p" ]]; then
    if chmod 600 "$p" 2>/dev/null || chmod u=rw,go= "$p" 2>/dev/null; then
      echo "chmod 600 $p"
    else
      echo "skip (not owner): $p — run: sudo chmod 600 $p"
    fi
  fi
done

# Tunnel JSON: cloudflared image runs as UID 65532 and must read bind-mounted creds — use 644 on the file,
# 755 on the directory so the container user can traverse to the JSON (700 breaks cloudflared after restart).
if [[ -d "${ROOT}/data/cloudflared/credentials" ]]; then
  chmod 755 "${ROOT}/data/cloudflared/credentials"
  echo "chmod 755 ${ROOT}/data/cloudflared/credentials"
  shopt -s nullglob
  for f in "${ROOT}/data/cloudflared/credentials/"*.json; do
    chmod 644 "$f"
    echo "chmod 644 $f  (tunnel cred; readable by cloudflared container UID)"
  done
fi
