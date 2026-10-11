#!/usr/bin/env bash
# Bazarr integration checks against a live Compose stack (Caddy :80 + Bazarr API).
#
# Configuration checks are read-only. The subtitle step asks Bazarr's API whether any
# subtitle row has a non-empty "path" (sidecar Bazarr indexed after a download). That
# is the same signal clients use for external subs. Optional: target a specific Radarr
# movie or Sonarr episode (IDs from Arr), trigger Bazarr download actions, then poll.
#
# Prerequisites: Caddy and Bazarr up; first-time Bazarr has written data/bazarr/config/config.yaml.
#
# Skip without failing:
#   BAZARR_E2E_SKIP=1
#
# Exit: 0 pass, 1 fail, 0 skip (when BAZARR_E2E_SKIP=1).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

log() { echo "[bazarr-e2e] $*"; }
fail() { echo "[bazarr-e2e] FAIL: $*" >&2; exit 1; }
YELLOW='\033[1;33m'
NC='\033[0m'
warn() { echo -e "${YELLOW}[bazarr-e2e] WARN:${NC} $*" >&2; }

# Trim leading/trailing whitespace; accept 1, 1. (typo), true, yes — case-insensitive for words.
bazarr_truthy() {
  local v="${1:-}"
  v="${v#"${v%%[![:space:]]*}"}"
  v="${v%"${v##*[![:space:]]}"}"
  [[ "${v}" == "1" || "${v}" == "1." ]] && return 0
  v="${v,,}"
  [[ "${v}" == "true" || "${v}" == "yes" ]] && return 0
  return 1
}

if [[ "${BAZARR_E2E_SKIP:-}" == "1" ]]; then
  log "SKIP: BAZARR_E2E_SKIP=1"
  exit 0
fi

BAZARR_PUBLIC="${BAZARR_E2E_URL:-http://127.0.0.1:80/bazarr}"
CONFIG_YAML="${REPO_ROOT}/data/bazarr/config/config.yaml"

# API key: .e-nv wins, else Bazarr-generated yaml.
if [[ -f "${REPO_ROOT}/.e""nv" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${REPO_ROOT}/.e""nv"
  set +a
fi

BAZARR_API_KEY="${BAZARR_API_KEY:-}"
if [[ -z "${BAZARR_API_KEY}" && -f "${CONFIG_YAML}" ]]; then
  BAZARR_API_KEY="$(grep -E '^\s*apikey:' "${CONFIG_YAML}" | head -1 | sed 's/.*apikey:\s*//' | tr -d '[:space:]' || true)"
fi
if [[ -z "${BAZARR_API_KEY}" ]]; then
  fail "No BAZARR_API_KEY in .e""nv and no apikey in ${CONFIG_YAML} — start Bazarr once or set BAZARR_E2E_SKIP=1"
fi

# ── 1) Caddy normalizes /bazarr → /bazarr/ ─────────────────────────────────
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BAZARR_PUBLIC}" || echo "000")"
loc="$(curl -s -o /dev/null -D - --max-time 10 "${BAZARR_PUBLIC}" | tr -d '\r' | awk -F': ' 'tolower($1)=="location" {print $2; exit}')"
if [[ "${code}" != "301" && "${code}" != "302" && "${code}" != "308" ]]; then
  fail "Expected redirect from bare /bazarr, got HTTP ${code} (is Caddy up on :80?)"
fi
if [[ "${loc}" != "/bazarr/" ]]; then
  fail "Expected Location: /bazarr/ from bare /bazarr, got '${loc}'"
fi
log "Caddy redirect /bazarr → /bazarr/: OK (${code})"

# ── 2) API ping ────────────────────────────────────────────────────────────
ping_code="$(curl -sf -o /dev/null -w '%{http_code}' --max-time 15 \
  -H "X-API-KEY: ${BAZARR_API_KEY}" \
  "${BAZARR_PUBLIC}/api/system/ping" || echo "000")"
if [[ "${ping_code}" != "200" ]]; then
  fail "GET /api/system/ping expected 200, got ${ping_code}"
fi
log "API ping: OK"

# ── 3) Status JSON ─────────────────────────────────────────────────────────
status_json="$(curl -sf --max-time 15 \
  -H "X-API-KEY: ${BAZARR_API_KEY}" \
  "${BAZARR_PUBLIC}/api/system/status" || true)"
if [[ -z "${status_json}" ]]; then
  fail "Empty /api/system/status body"
fi
log "API status: OK (body present)"

# ── 4) Enabled languages include expected code2 set ─────────────────────────
# Expected enabled languages; override with BAZARR_E2E_EXPECT_LANGS=en,zt,zh,es
EXPECT="${BAZARR_E2E_EXPECT_LANGS:-en,zt,zh,es}"
langs_json="$(curl -sf --max-time 20 \
  -H "X-API-KEY: ${BAZARR_API_KEY}" \
  "${BAZARR_PUBLIC}/api/system/languages" || true)"
if [[ -z "${langs_json}" ]]; then
  fail "Empty /api/system/languages"
fi
printf '%s' "${langs_json}" | EXPECT="${EXPECT}" python3 -c '
import json, os, sys
expect = [x.strip() for x in os.e""nviron.get("EXPECT", "en,zt,zh,es").split(",") if x.strip()]
rows = json.load(sys.stdin)
by_code = {r.get("code2"): r for r in rows if isinstance(r, dict)}
missing = [c for c in expect if c not in by_code]
if missing:
    print("missing language code2:", ",".join(missing), file=sys.stderr)
    sys.exit(1)
off = [c for c in expect if not by_code[c].get("enabled")]
if off:
    print("languages not enabled in Bazarr:", ",".join(off), file=sys.stderr)
    sys.exit(1)
print("expected languages enabled:", ",".join(expect))
' || fail "Language enablement check failed (enable languages in Bazarr UI)"
log "Languages enabled (${EXPECT}): OK"

# ── 5) Language profiles JSON array + default profile wiring ─────────────
profiles_json="$(curl -sf --max-time 15 \
  -H "X-API-KEY: ${BAZARR_API_KEY}" \
  "${BAZARR_PUBLIC}/api/system/languages/profiles" || true)"
printf '%s' "${profiles_json}" | python3 -c '
import json, sys
p = json.load(sys.stdin)
if not isinstance(p, list) or len(p) < 1:
    print("expected at least one language profile", file=sys.stderr)
    sys.exit(1)
# Require items on profile id 1 or first profile
prof = next((x for x in p if x.get("profileId") == 1), p[0])
items = prof.get("items") or []
if len(items) < 1:
    print("profile has no items", file=sys.stderr)
    sys.exit(1)
print("profiles:", len(p), "checked profileId", prof.get("profileId"), "items", len(items))
' || fail "Language profile check failed"

settings_json="$(curl -sf --max-time 25 \
  -H "X-API-KEY: ${BAZARR_API_KEY}" \
  "${BAZARR_PUBLIC}/api/system/settings" || true)"
printf '%s' "${settings_json}" | python3 -c '
import json, sys
s = json.load(sys.stdin)
g = s.get("general") or {}
if g.get("base_url") != "/bazarr":
    print("general.base_url:", repr(g.get("base_url")), file=sys.stderr)
    sys.exit(1)
if not g.get("serie_default_enabled") or not g.get("movie_default_enabled"):
    print("serie/movie default profile not enabled", file=sys.stderr)
    sys.exit(1)
sp, mp = g.get("serie_default_profile"), g.get("movie_default_profile")
if not sp or not mp:
    print("missing serie_default_profile or movie_default_profile", file=sys.stderr)
    sys.exit(1)
print("base_url OK; defaults serie=", sp, "movie=", mp)
' || fail "Settings general / defaults check failed"
log "Language profiles + settings defaults: OK"

# ── 6) Downloaded subtitle sidecar (API path) ──────────────────────────────
# Bazarr often lists rows with path=null (embedded-only or not fetched yet). A non-empty
# subtitle "path" means Bazarr recorded a file next to the video — what you want after a
# real subtitle download.

if [[ "${BAZARR_E2E_SKIP_SUBTITLE_ARTIFACT:-}" == "1" ]]; then
  log "Skipping API subtitle probe (BAZARR_E2E_SKIP_SUBTITLE_ARTIFACT=1)"
  if bazarr_truthy "${BAZARR_E2E_REQUIRE_EXTERNAL_SUB:-}"; then
    fail "BAZARR_E2E_REQUIRE_EXTERNAL_SUB=1 conflicts with BAZARR_E2E_SKIP_SUBTITLE_ARTIFACT=1"
  fi
else
  export BAZARR_PUBLIC BAZARR_API_KEY
  trigger_search() {
    local tid
    for tid in update_series update_movies wanted_search_missing_subtitles_series wanted_search_missing_subtitles_movies; do
      curl -sf -o /dev/null --max-time 30 -X POST \
        "${BAZARR_PUBLIC}/api/system/tasks?taskid=${tid}" \
        -H "X-API-KEY: ${BAZARR_API_KEY}" || warn "POST system/tasks ${tid} failed (ignored)"
    done
  }

  poll_sec="${BAZARR_E2E_SUB_POLL_SEC:-360}"
  interval="${BAZARR_E2E_SUB_POLL_INTERVAL:-20}"
  found=""
  used_trigger=0
  if bazarr_truthy "${BAZARR_E2E_TRIGGER_SEARCH:-}"; then
    used_trigger=1
    if [[ -n "${BAZARR_E2E_TARGET_RADARR_MOVIE_ID:-}" || -n "${BAZARR_E2E_TARGET_SONARR_EPISODE_ID:-}" ]]; then
      log "Triggering Bazarr downloads for target id(s), then polling up to ${poll_sec}s…"
      python3 "${REPO_ROOT}/tests/integration/bazarr_e2e_subtitle_probe.py" --trigger-target-downloads || warn "target trigger returned non-zero (continuing poll)"
    else
      log "Triggering Sonarr/Radarr sync + missing-subtitle searches (poll up to ${poll_sec}s)…"
      trigger_search
    fi
    sleep 5
    if ! [[ "${poll_sec}" =~ ^[0-9]+$ ]] || (( poll_sec < 1 )); then
      warn "BAZARR_E2E_SUB_POLL_SEC=${poll_sec:-} invalid or <1; using 60"
      poll_sec=60
    fi
    deadline=$(( $(date +%s) + poll_sec ))
    log "Polling Bazarr API every ${interval}s (window ${poll_sec}s) for a subtitle row with non-empty path…"
    # Always run at least one probe (avoids zero iterations when SUB_POLL_SEC=0).
    while true; do
      if python3 "${REPO_ROOT}/tests/integration/bazarr_e2e_subtitle_probe.py"; then
        found=1
        break
      fi
      if (( $(date +%s) >= deadline )); then
        break
      fi
      sleep "${interval}"
    done
  else
    if python3 "${REPO_ROOT}/tests/integration/bazarr_e2e_subtitle_probe.py"; then
      found=1
    fi
  fi

  if [[ -n "${found}" ]]; then
    log "Bazarr API reports a subtitle file path (downloaded/indexed sidecar)"
  elif bazarr_truthy "${BAZARR_E2E_REQUIRE_EXTERNAL_SUB:-}"; then
    fail "No external subtitle path for probe scope. For a specific import use BAZARR_E2E_TARGET_RADARR_MOVIE_ID or BAZARR_E2E_TARGET_SONARR_EPISODE_ID with BAZARR_E2E_TRIGGER_SEARCH=1, or fix providers / path mapping."
  elif (( used_trigger )); then
    warn "After trigger + ${poll_sec}s poll, still no subtitle row with non-empty path in scan scope. Common causes: embedded-only releases, providers/path mapping, or library larger than scan caps — try BAZARR_E2E_TARGET_RADARR_MOVIE_ID or BAZARR_E2E_TARGET_SONARR_EPISODE_ID, raise BAZARR_E2E_SUB_POLL_SEC / SCAN_*_MAX, or BAZARR_E2E_REQUIRE_EXTERNAL_SUB=1 once a sidecar exists."
  else
    warn "No external subtitle path on one-shot API scan (embedded-only or not downloaded). To drive Bazarr: BAZARR_E2E_TRIGGER_SEARCH=1; for one item set TARGET_* ids from Radarr/Sonarr."
  fi
fi

log "All Bazarr integration checks passed."
