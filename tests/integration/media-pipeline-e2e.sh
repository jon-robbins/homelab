#!/usr/bin/env bash
# End-to-end media pipeline test: Overseerr -> Radarr/Sonarr -> qBittorrent
# Requests a well-known movie (Star Wars) and TV show (Breaking Bad S01),
# verifies they reach qBittorrent, then cleans up all records.
#
# Requires: all media containers running, .env sourced or present.
# Exit 0 = pass, non-zero = fail.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

# ── Load config ──────────────────────────────────────────────────────────────
set -a
# shellcheck source=/dev/null
source "${REPO_ROOT}/.env"
set +a

RADARR_API_KEY="${RADARR_API_KEY}"
SONARR_API_KEY="${SONARR_API_KEY}"
QB_USER="${QBITTORRENT_USERNAME}"
QB_PASS="${QBITTORRENT_PASSWORD}"

# Test targets: well-seeded, universally available content.
MOVIE_TMDB_ID=11        # Star Wars (1977)
MOVIE_TITLE="Star Wars"
TV_TMDB_ID=1396          # Breaking Bad
TV_TITLE="Breaking Bad"
TV_SEASONS='[1]'         # Only request season 1 to keep it fast

POLL_INTERVAL=5
POLL_TIMEOUT=120  # seconds

# ── Helpers ──────────────────────────────────────────────────────────────────
COMPOSE_PROJECT="${COMPOSE_PROJECT:-homelab}"
cexec() { docker compose -p "${COMPOSE_PROJECT}" exec -T "$@"; }
log()  { echo "[media-e2e] $*"; }
fail() { echo "[media-e2e] FAIL: $*" >&2; cleanup; exit 1; }

seerr_api() {
  # Call Overseerr via docker compose exec to avoid host port dependency.
  local method="$1" path="$2"; shift 2
  local api_key
  api_key="$(cexec overseerr sh -c 'node -e "const f=require(\"fs\");const c=JSON.parse(f.readFileSync(\"/app/config/settings.json\",\"utf8\"));console.log(c.main.apiKey);"' 2>/dev/null)"
  cexec overseerr sh -c \
    "curl -sf -X '${method}' -H 'X-Api-Key: ${api_key}' -H 'Content-Type: application/json' 'http://localhost:5055${path}' $*"
}

radarr_api() {
  local method="$1" path="$2"; shift 2
  cexec radarr curl -sf -X "${method}" \
    -H "X-Api-Key: ${RADARR_API_KEY}" \
    -H "Content-Type: application/json" \
    "http://localhost:7878/radarr${path}" "$@"
}

sonarr_api() {
  local method="$1" path="$2"; shift 2
  cexec sonarr curl -sf -X "${method}" \
    -H "X-Api-Key: ${SONARR_API_KEY}" \
    -H "Content-Type: application/json" \
    "http://localhost:8989/sonarr${path}" "$@"
}

qbt_api() {
  # Authenticates and runs a qBittorrent API call inside the container.
  local path="$1"; shift
  cexec qbittorrent sh -c "
    COOKIE=/tmp/qbt_e2e_cookie
    curl -sf -c \"\${COOKIE}\" -X POST \
      --data-urlencode 'username=${QB_USER}' \
      --data-urlencode 'password=${QB_PASS}' \
      http://localhost:8080/api/v2/auth/login >/dev/null 2>&1
    curl -sf -b \"\${COOKIE}\" 'http://localhost:8080/api/v2${path}' \"\$@\"
  " -- "$@"
}

# ── Track IDs for cleanup ───────────────────────────────────────────────────
SEERR_MOVIE_REQUEST_ID=""
SEERR_MOVIE_MEDIA_ID=""
SEERR_TV_REQUEST_ID=""
SEERR_TV_MEDIA_ID=""
RADARR_MOVIE_ID=""
SONARR_SERIES_ID=""
QBT_MOVIE_HASHES=""
QBT_TV_HASHES=""

cleanup() {
  log "Cleaning up..."
  local rc=0

  # 1. Remove torrents from qBittorrent (delete data too)
  for hashes_var in QBT_MOVIE_HASHES QBT_TV_HASHES; do
    local hashes="${!hashes_var}"
    if [[ -n "${hashes}" ]]; then
      log "  Removing qBittorrent torrents: ${hashes}"
      cexec qbittorrent sh -c "
        COOKIE=/tmp/qbt_e2e_cookie
        curl -sf -c \"\${COOKIE}\" -X POST \
          --data-urlencode 'username=${QB_USER}' \
          --data-urlencode 'password=${QB_PASS}' \
          http://localhost:8080/api/v2/auth/login >/dev/null 2>&1
        curl -sf -b \"\${COOKIE}\" -X POST \
          --data-urlencode 'hashes=${hashes}' \
          --data-urlencode 'deleteFiles=true' \
          http://localhost:8080/api/v2/torrents/delete
      " 2>/dev/null || log "  warn: qBittorrent torrent removal failed"
    fi
  done

  # 2. Remove from Radarr
  if [[ -n "${RADARR_MOVIE_ID}" ]]; then
    log "  Removing Radarr movie id=${RADARR_MOVIE_ID}"
    radarr_api DELETE "/api/v3/movie/${RADARR_MOVIE_ID}?deleteFiles=true&addImportExclusion=false" \
      2>/dev/null || log "  warn: Radarr movie removal failed"
  fi

  # 3. Remove from Sonarr
  if [[ -n "${SONARR_SERIES_ID}" ]]; then
    log "  Removing Sonarr series id=${SONARR_SERIES_ID}"
    sonarr_api DELETE "/api/v3/series/${SONARR_SERIES_ID}?deleteFiles=true&addImportExclusion=false" \
      2>/dev/null || log "  warn: Sonarr series removal failed"
  fi

  # 4. Remove Overseerr media entries (this also clears requests)
  if [[ -n "${SEERR_MOVIE_MEDIA_ID}" ]]; then
    log "  Removing Overseerr movie media id=${SEERR_MOVIE_MEDIA_ID}"
    seerr_api DELETE "/api/v1/media/${SEERR_MOVIE_MEDIA_ID}" 2>/dev/null \
      || log "  warn: Overseerr movie media removal failed"
  fi
  if [[ -n "${SEERR_TV_MEDIA_ID}" ]]; then
    log "  Removing Overseerr TV media id=${SEERR_TV_MEDIA_ID}"
    seerr_api DELETE "/api/v1/media/${SEERR_TV_MEDIA_ID}" 2>/dev/null \
      || log "  warn: Overseerr TV media removal failed"
  fi

  log "Cleanup complete."
}

# ── Pre-flight: verify services are reachable ────────────────────────────────
log "Checking service connectivity..."
seerr_api GET "/api/v1/status" >/dev/null       || fail "Overseerr unreachable"
radarr_api GET "/api/v3/system/status" >/dev/null || fail "Radarr unreachable"
sonarr_api GET "/api/v3/system/status" >/dev/null || fail "Sonarr unreachable"
qbt_api "/app/version" >/dev/null                 || fail "qBittorrent unreachable"
log "All services reachable."

# ── Step 1: Request movie via Overseerr ──────────────────────────────────────
log "Requesting movie: ${MOVIE_TITLE} (tmdb=${MOVIE_TMDB_ID})..."
movie_resp="$(seerr_api POST "/api/v1/request" \
  -d "{\"mediaType\":\"movie\",\"mediaId\":${MOVIE_TMDB_ID}}")" \
  || fail "Movie request failed"

SEERR_MOVIE_REQUEST_ID="$(echo "${movie_resp}" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")"
SEERR_MOVIE_MEDIA_ID="$(echo "${movie_resp}" | python3 -c "import json,sys; print(json.load(sys.stdin)['media']['id'])")"
log "  Overseerr request id=${SEERR_MOVIE_REQUEST_ID}, media id=${SEERR_MOVIE_MEDIA_ID}"

# ── Step 2: Request TV show via Overseerr ────────────────────────────────────
log "Requesting TV: ${TV_TITLE} S01 (tmdb=${TV_TMDB_ID})..."
tv_resp="$(seerr_api POST "/api/v1/request" \
  -d "{\"mediaType\":\"tv\",\"mediaId\":${TV_TMDB_ID},\"seasons\":${TV_SEASONS}}")" \
  || fail "TV request failed"

SEERR_TV_REQUEST_ID="$(echo "${tv_resp}" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")"
SEERR_TV_MEDIA_ID="$(echo "${tv_resp}" | python3 -c "import json,sys; print(json.load(sys.stdin)['media']['id'])")"
log "  Overseerr request id=${SEERR_TV_REQUEST_ID}, media id=${SEERR_TV_MEDIA_ID}"

# ── Step 3: Wait for Radarr to pick up the movie ────────────────────────────
log "Waiting for Radarr to receive the movie..."
elapsed=0
while [[ ${elapsed} -lt ${POLL_TIMEOUT} ]]; do
  radarr_movie="$(radarr_api GET "/api/v3/movie?tmdbId=${MOVIE_TMDB_ID}" 2>/dev/null || echo "[]")"
  RADARR_MOVIE_ID="$(echo "${radarr_movie}" | python3 -c "
import json,sys
data = json.load(sys.stdin)
print(data[0]['id'] if data else '')
" 2>/dev/null || echo "")"
  if [[ -n "${RADARR_MOVIE_ID}" ]]; then
    log "  Radarr has movie id=${RADARR_MOVIE_ID}"
    break
  fi
  sleep "${POLL_INTERVAL}"
  elapsed=$((elapsed + POLL_INTERVAL))
done
[[ -n "${RADARR_MOVIE_ID}" ]] || fail "Radarr did not receive the movie within ${POLL_TIMEOUT}s"

# ── Step 4: Wait for Sonarr to pick up the show ─────────────────────────────
log "Waiting for Sonarr to receive the TV show..."
elapsed=0
while [[ ${elapsed} -lt ${POLL_TIMEOUT} ]]; do
  sonarr_series="$(sonarr_api GET "/api/v3/series?tvdbId=81189" 2>/dev/null || echo "[]")"
  # Breaking Bad tvdbId=81189; also try lookup by title as fallback
  SONARR_SERIES_ID="$(echo "${sonarr_series}" | python3 -c "
import json,sys
data = json.load(sys.stdin)
print(data[0]['id'] if data else '')
" 2>/dev/null || echo "")"
  if [[ -z "${SONARR_SERIES_ID}" ]]; then
    # Fallback: search all series
    sonarr_series="$(sonarr_api GET "/api/v3/series" 2>/dev/null || echo "[]")"
    SONARR_SERIES_ID="$(echo "${sonarr_series}" | python3 -c "
import json,sys
data = json.load(sys.stdin)
for s in data:
    if s.get('tvdbId') == 81189 or 'breaking bad' in s.get('title','').lower():
        print(s['id']); break
else:
    print('')
" 2>/dev/null || echo "")"
  fi
  if [[ -n "${SONARR_SERIES_ID}" ]]; then
    log "  Sonarr has series id=${SONARR_SERIES_ID}"
    break
  fi
  sleep "${POLL_INTERVAL}"
  elapsed=$((elapsed + POLL_INTERVAL))
done
[[ -n "${SONARR_SERIES_ID}" ]] || fail "Sonarr did not receive the TV show within ${POLL_TIMEOUT}s"

# ── Step 5: Wait for torrents to appear in qBittorrent ───────────────────────
log "Waiting for torrents in qBittorrent..."
elapsed=0
movie_found=false
tv_found=false

while [[ ${elapsed} -lt ${POLL_TIMEOUT} ]]; do
  torrents="$(qbt_api "/torrents/info" 2>/dev/null || echo "[]")"

  if [[ "${movie_found}" != "true" ]]; then
    QBT_MOVIE_HASHES="$(echo "${torrents}" | python3 -c "
import json,sys
data = json.load(sys.stdin)
hashes = []
for t in data:
    cat = (t.get('category') or '').lower()
    name = (t.get('name') or '').lower()
    if 'radarr' in cat and ('star' in name and 'war' in name):
        hashes.append(t['hash'])
print('|'.join(hashes))
" 2>/dev/null || echo "")"
    if [[ -n "${QBT_MOVIE_HASHES}" ]]; then
      movie_found=true
      log "  Movie torrent found in qBittorrent: ${QBT_MOVIE_HASHES}"
    fi
  fi

  if [[ "${tv_found}" != "true" ]]; then
    QBT_TV_HASHES="$(echo "${torrents}" | python3 -c "
import json,sys
data = json.load(sys.stdin)
hashes = []
for t in data:
    cat = (t.get('category') or '').lower()
    name = (t.get('name') or '').lower()
    if 'sonarr' in cat and 'breaking' in name and 'bad' in name:
        hashes.append(t['hash'])
print('|'.join(hashes))
" 2>/dev/null || echo "")"
    if [[ -n "${QBT_TV_HASHES}" ]]; then
      tv_found=true
      log "  TV torrent found in qBittorrent: ${QBT_TV_HASHES}"
    fi
  fi

  if [[ "${movie_found}" == "true" && "${tv_found}" == "true" ]]; then
    break
  fi

  sleep "${POLL_INTERVAL}"
  elapsed=$((elapsed + POLL_INTERVAL))
done

# ── Evaluate results ─────────────────────────────────────────────────────────
passed=true
if [[ "${movie_found}" != "true" ]]; then
  log "FAIL: Movie torrent not found in qBittorrent within ${POLL_TIMEOUT}s"
  # Check Radarr queue for details
  radarr_api GET "/api/v3/queue" 2>/dev/null | python3 -c "
import json,sys
data = json.load(sys.stdin)
for r in data.get('records', []):
    print(f'  Radarr queue: {r.get(\"title\")} status={r.get(\"status\")} trackedDownloadStatus={r.get(\"trackedDownloadStatus\",\"\")}')
" 2>/dev/null || true
  passed=false
fi

if [[ "${tv_found}" != "true" ]]; then
  log "FAIL: TV torrent not found in qBittorrent within ${POLL_TIMEOUT}s"
  sonarr_api GET "/api/v3/queue" 2>/dev/null | python3 -c "
import json,sys
data = json.load(sys.stdin)
for r in data.get('records', []):
    print(f'  Sonarr queue: {r.get(\"title\")} status={r.get(\"status\")} trackedDownloadStatus={r.get(\"trackedDownloadStatus\",\"\")}')
" 2>/dev/null || true
  passed=false
fi

# ── Cleanup ──────────────────────────────────────────────────────────────────
cleanup

if [[ "${passed}" == "true" ]]; then
  log "PASS: Full pipeline verified (Overseerr -> Radarr/Sonarr -> qBittorrent)"
  exit 0
else
  exit 1
fi
