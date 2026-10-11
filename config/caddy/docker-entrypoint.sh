#!/bin/sh
set -eu
SECRET_FILE="${CLOUDFLARE_TOKEN_FILE:-/run/secrets/CLOUDFLARE_TOKEN}"
if [ -f "$SECRET_FILE" ]; then
  # Secrets are single-line; strip CR/LF characters from the file contents.
  CLOUDFLARE_TOKEN="$(tr -d '\r\n' <"$SECRET_FILE")"
  export CLOUDFLARE_TOKEN
fi
exec /usr/bin/caddy "$@"
