#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# configure-bazarr.sh – Post-boot Bazarr configuration via REST API
# ---------------------------------------------------------------------------
# NOTE: Bazarr stores settings in a SQLite database initialised on first boot.
#       Pre-seeding config files is NOT supported.  This script must run AFTER
#       the container has started and its API is responsive.
#
# NOTE: The exact Bazarr API shape may vary between versions.  The calls below
#       target the /api/system/settings endpoint used by Bazarr v1.x.  If a
#       future release changes the API contract, update the payloads below.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

# ── Colours & helpers ─────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Best-effort API call wrapper.  Logs warnings on failure but never aborts.
api_post() {
  local desc="$1"; shift
  local endpoint="$1"; shift
  local body="$1"; shift

  local http_code
  http_code=$(curl -sf -o /dev/null -w '%{http_code}' --max-time 15 \
    -X POST "${BAZARR_URL}${endpoint}" \
    -H "X-API-KEY: ${BAZARR_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$body" 2>/dev/null || echo "000")

  if [[ "$http_code" =~ ^2 ]]; then
    info "${desc}: OK (${http_code})"
    return 0
  else
    warn "${desc}: FAILED (HTTP ${http_code})"
    return 1
  fi
}

# ── 1. Source .env ────────────────────────────────────────────────────────
ENV_FILE="${REPO_ROOT}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  error ".env not found at ${ENV_FILE}"
  exit 1
fi
set -a; source "$ENV_FILE"; set +a
info "Loaded .env"

: "${SONARR_API_KEY:?SONARR_API_KEY not set in .env}"
: "${RADARR_API_KEY:?RADARR_API_KEY not set in .env}"

# ── 2. Extract Plex token ────────────────────────────────────────────────
PLEX_PREFS="${REPO_ROOT}/data/plex/Library/Application Support/Plex Media Server/Preferences.xml"
if [[ -f "$PLEX_PREFS" ]]; then
  PLEX_TOKEN=$(grep -oP 'PlexOnlineToken="\K[^"]+' "$PLEX_PREFS" || true)
  if [[ -n "$PLEX_TOKEN" ]]; then
    info "Extracted Plex token (${#PLEX_TOKEN} chars)"
  else
    warn "PlexOnlineToken not found in Preferences.xml — Plex integration will be skipped"
  fi
else
  warn "Plex Preferences.xml not found — Plex integration will be skipped"
  PLEX_TOKEN=""
fi

# ── 3. Wait for Bazarr to become responsive ──────────────────────────────
# Bazarr sits on homelab_net with no host port mapping, so reach it via
# the Caddy reverse proxy listening on localhost:80.
BAZARR_URL="http://localhost:80/bazarr"
TIMEOUT=120
INTERVAL=5

info "Waiting for Bazarr API at ${BAZARR_URL} (timeout ${TIMEOUT}s)..."
elapsed=0
while (( elapsed < TIMEOUT )); do
  # Try the two common status/ping endpoints.
  if curl -sf --max-time 5 "${BAZARR_URL}/api/system/status" >/dev/null 2>&1; then
    break
  fi
  if curl -sf --max-time 5 "${BAZARR_URL}/api/system/ping" >/dev/null 2>&1; then
    break
  fi
  printf "."
  sleep "$INTERVAL"
  elapsed=$((elapsed + INTERVAL))
done
echo ""

if (( elapsed >= TIMEOUT )); then
  error "Bazarr did not become responsive within ${TIMEOUT}s"
  exit 1
fi
info "Bazarr is responsive"

# ── 4. Extract Bazarr API key ────────────────────────────────────────────
# The volume maps ../data/bazarr:/config inside the container.
# Bazarr writes its API key into /config/config/config.yaml.
BAZARR_CONFIG="${REPO_ROOT}/data/bazarr/config/config.yaml"
if [[ ! -f "$BAZARR_CONFIG" ]]; then
  error "Bazarr config not found at ${BAZARR_CONFIG} — has the container started at least once?"
  exit 1
fi

BAZARR_API_KEY=$(grep -E '^\s*apikey:' "$BAZARR_CONFIG" | head -1 | sed 's/.*apikey:\s*//' | tr -d '[:space:]' || true)
if [[ -z "$BAZARR_API_KEY" ]]; then
  # Fallback: try alternate key names
  BAZARR_API_KEY=$(grep -oP 'api_key:\s*\K\S+' "$BAZARR_CONFIG" || true)
fi

if [[ -z "$BAZARR_API_KEY" ]]; then
  error "Could not extract Bazarr API key from ${BAZARR_CONFIG}"
  exit 1
fi
info "Extracted Bazarr API key (${#BAZARR_API_KEY} chars)"

# ── 5. Configure Bazarr via API ──────────────────────────────────────────

# 5a. General settings — set base URL so Bazarr knows it lives under /bazarr.
api_post "General: base_url" "/api/system/settings" \
  '{"settings": {"general": {"base_url": "/bazarr"}}}'

# 5b. Sonarr connection
api_post "Sonarr connection" "/api/system/settings" \
  "$(cat <<JSON
{"settings": {"sonarr": {
  "ip": "sonarr",
  "port": 8989,
  "base_url": "/sonarr",
  "apikey": "${SONARR_API_KEY}",
  "enabled": true
}}}
JSON
)"

# 5c. Radarr connection
api_post "Radarr connection" "/api/system/settings" \
  "$(cat <<JSON
{"settings": {"radarr": {
  "ip": "radarr",
  "port": 7878,
  "base_url": "/radarr",
  "apikey": "${RADARR_API_KEY}",
  "enabled": true
}}}
JSON
)"

# 5d. Plex connection (Plex runs in host network mode → host.docker.internal)
if [[ -n "$PLEX_TOKEN" ]]; then
  api_post "Plex connection" "/api/system/settings" \
    "$(cat <<JSON
{"settings": {"plex": {
  "ip": "host.docker.internal",
  "port": 32400,
  "ssl": false,
  "apikey": "${PLEX_TOKEN}"
}}}
JSON
)"
else
  warn "Skipping Plex configuration (no token available)"
fi

# 5e. Subtitle language — set English as default.
api_post "Subtitle languages (English)" "/api/system/settings" \
  '{"settings": {"general": {"enabled_languages": ["en"]}}}'

# ── 6. Write BAZARR_API_KEY back to .env ─────────────────────────────────
if grep -qE "^BAZARR_API_KEY=" "$ENV_FILE"; then
  sed -i "s|^BAZARR_API_KEY=.*|BAZARR_API_KEY=${BAZARR_API_KEY}|" "$ENV_FILE"
else
  printf '\n# Bazarr\nBAZARR_API_KEY=%s\n' "$BAZARR_API_KEY" >> "$ENV_FILE"
fi
info "Wrote BAZARR_API_KEY to .env"

# ── 7. Summary ───────────────────────────────────────────────────────────
echo ""
info "══════════════════════════════════════════"
info "  Bazarr configuration complete"
info "══════════════════════════════════════════"
echo ""
info "Configured:"
echo "  • Base URL:    /bazarr"
echo "  • Sonarr:      sonarr:8989/sonarr"
echo "  • Radarr:      radarr:7878/radarr"
if [[ -n "$PLEX_TOKEN" ]]; then
  echo "  • Plex:        host.docker.internal:32400"
fi
echo "  • Languages:   English (en)"
echo "  • API key:     saved to .env"
echo ""
warn "Manual steps remaining:"
echo "  1. Add subtitle provider credentials (e.g. OpenSubtitles) via the Bazarr UI"
echo "     → ${BAZARR_URL}/settings/providers"
echo "  2. Configure subtitle profiles for your libraries"
echo "     → ${BAZARR_URL}/settings/languages"
echo ""
info "Done."
