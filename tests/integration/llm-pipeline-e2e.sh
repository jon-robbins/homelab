#!/usr/bin/env bash
# End-to-end LLM pipeline test: openclaw-gateway -> media-agent router -> Prowlarr -> qBittorrent
#
# Sends a natural-language download request through the LLM router, picks an
# option from the results, verifies the torrent lands in qBittorrent, then
# cleans up all records for reproducibility.
#
# Requires: openclaw-gateway, media-agent, prowlarr, qbittorrent containers running.
# Exit 0 = pass, non-zero = fail.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

# ── Load config ──────────────────────────────────────────────────────────────
set -a
# shellcheck source=/dev/null
source "${REPO_ROOT}/.env"
set +a

QB_USER="${QBITTORRENT_USERNAME}"
QB_PASS="${QBITTORRENT_PASSWORD}"

POLL_INTERVAL=5
POLL_TIMEOUT=120

REQUEST_TEXT="${CI_LOCAL_LLM_REQUEST:-Download The Matrix 1999}"
SESSION_KEY="ci-llm-e2e-$(date +%s)"

# ── Helpers ──────────────────────────────────────────────────────────────────
COMPOSE_PROJECT="${COMPOSE_PROJECT:-homelab}"
cexec() { docker compose -p "${COMPOSE_PROJECT}" exec -T "$@"; }
log()  { echo "[llm-e2e] $*"; }
fail() { echo "[llm-e2e] FAIL: $*" >&2; cleanup; exit 1; }

qbt_login_and_call() {
  # Authenticates and runs a qBittorrent API call inside the container.
  local path="$1"; shift
  cexec qbittorrent sh -c "
    COOKIE=/tmp/qbt_llm_e2e_cookie
    curl -sf -c \"\${COOKIE}\" -X POST \
      --data-urlencode 'username=${QB_USER}' \
      --data-urlencode 'password=${QB_PASS}' \
      http://localhost:8080/api/v2/auth/login >/dev/null 2>&1
    curl -sf -b \"\${COOKIE}\" 'http://localhost:8080/api/v2${path}' \"\$@\"
  " -- "$@"
}

# ── Track state for cleanup ─────────────────────────────────────────────────
QBT_HASHES=""

cleanup() {
  log "Cleaning up..."
  if [[ -n "${QBT_HASHES}" ]]; then
    log "  Removing qBittorrent torrents: ${QBT_HASHES}"
    local hash
    # QBT_HASHES is pipe-delimited; delete each one
    IFS='|' read -ra hash_arr <<< "${QBT_HASHES}"
    for hash in "${hash_arr[@]}"; do
      cexec qbittorrent sh -c "
        COOKIE=/tmp/qbt_llm_e2e_cookie
        curl -sf -c \"\${COOKIE}\" -X POST \
          --data-urlencode 'username=${QB_USER}' \
          --data-urlencode 'password=${QB_PASS}' \
          http://localhost:8080/api/v2/auth/login >/dev/null 2>&1
        curl -sf -b \"\${COOKIE}\" -X POST \
          --data-urlencode 'hashes=${hash}' \
          --data-urlencode 'deleteFiles=true' \
          http://localhost:8080/api/v2/torrents/delete
      " 2>/dev/null || log "  warn: failed to remove torrent ${hash}"
    done
  fi
  log "Cleanup complete."
}

# ── Pre-flight: verify services are reachable ────────────────────────────────
log "Checking service connectivity..."
cexec openclaw-gateway sh -c 'echo ok' >/dev/null 2>&1 \
  || fail "openclaw-gateway container not running"
cexec media-agent sh -c 'echo ok' >/dev/null 2>&1 \
  || fail "media-agent container not running"
qbt_login_and_call "/app/version" >/dev/null \
  || fail "qBittorrent unreachable"
log "All services reachable."

# ── Step 1: Send request to LLM router ───────────────────────────────────────
log "Sending request to LLM router: '${REQUEST_TEXT}' (session=${SESSION_KEY})..."

router_out="$(
  docker compose -p "${COMPOSE_PROJECT}" exec -T \
    -e ROUTER_SESSION_KEY="${SESSION_KEY}" \
    -e ROUTER_REQUEST_TEXT="${REQUEST_TEXT}" \
    openclaw-gateway sh -lc 'node -e "
      (async()=>{
        const base=process.env.MEDIA_AGENT_URL;
        const tok=process.env.MEDIA_AGENT_TOKEN;
        const h={\"Authorization\":\"Bearer \"+tok,\"Content-Type\":\"application/json\"};
        const session=process.env.ROUTER_SESSION_KEY;
        const text=process.env.ROUTER_REQUEST_TEXT;
        const res=await fetch(base+\"/internal/media-agent/v1/router\",{
          method:\"POST\",headers:h,
          body:JSON.stringify({message:text,session_key:session})
        });
        const j=await res.json();
        console.log(JSON.stringify({status:res.status,...j}));
      })().catch(e=>{console.error(e.message);process.exit(1);});
    "'
)" || fail "Router request failed"

# Parse response
router_parsed="$(python3 - "${router_out}" <<'PY'
import json, sys
obj = json.loads(sys.argv[1])
status = obj.get("status", 0)
ok = obj.get("ok", False)
intent = (obj.get("intent") or {}).get("intent", "")
options = (obj.get("tool_result") or {}).get("options", [])
response_text = obj.get("response_text", "")
# Dump structured info
print(json.dumps({
    "status": status,
    "ok": ok,
    "intent": intent,
    "options_count": len(options),
    "response_text": response_text[:200],
    "first_option": options[0] if options else None,
}))
PY
)" || fail "Failed to parse router response"

log "  Router response: $(echo "${router_parsed}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"status={d[\"status\"]} ok={d[\"ok\"]} intent={d[\"intent\"]} options={d[\"options_count\"]}")')"

options_count="$(echo "${router_parsed}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["options_count"])')"
if [[ "${options_count}" -eq 0 ]]; then
  fail "Router returned 0 options for '${REQUEST_TEXT}'"
fi

# ── Step 2: Pick option 1 and send selection ─────────────────────────────────
log "Picking option 1 and sending selection..."

grab_out="$(
  docker compose -p "${COMPOSE_PROJECT}" exec -T \
    -e ROUTER_SESSION_KEY="${SESSION_KEY}" \
    openclaw-gateway sh -lc 'node -e "
      (async()=>{
        const base=process.env.MEDIA_AGENT_URL;
        const tok=process.env.MEDIA_AGENT_TOKEN;
        const h={\"Authorization\":\"Bearer \"+tok,\"Content-Type\":\"application/json\"};
        const session=process.env.ROUTER_SESSION_KEY;
        const res=await fetch(base+\"/internal/media-agent/v1/router\",{
          method:\"POST\",headers:h,
          body:JSON.stringify({message:\"1\",session_key:session})
        });
        const j=await res.json();
        console.log(JSON.stringify({status:res.status,...j}));
      })().catch(e=>{console.error(e.message);process.exit(1);});
    "'
)" || fail "Selection/grab request failed"

# Parse grab response
grab_ok="$(python3 - "${grab_out}" <<'PY'
import json, sys
obj = json.loads(sys.argv[1])
ok = (obj.get("tool_result") or {}).get("ok", False)
resp = obj.get("response_text", "")
error = obj.get("error", {})
print(json.dumps({"ok": ok, "response_text": resp[:300], "error": error}))
PY
)" || fail "Failed to parse grab response"

grab_tool_ok="$(echo "${grab_ok}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ok"])')"
grab_response="$(echo "${grab_ok}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["response_text"])')"
log "  Grab result: ok=${grab_tool_ok} response='${grab_response}'"

if [[ "${grab_tool_ok}" != "True" ]]; then
  # Check for reuse case (already in qBittorrent)
  if echo "${grab_response}" | grep -qi "already in qBittorrent"; then
    log "  Torrent already exists in qBittorrent (reuse). Treating as success."
  else
    grab_error="$(echo "${grab_ok}" | python3 -c 'import json,sys; e=json.load(sys.stdin).get("error",{}); print(f"code={e.get(\"code\",\"\")} msg={e.get(\"message\",\"\")[:200]}")')"
    fail "Grab failed: ${grab_error}"
  fi
fi

# ── Step 3: Wait for torrent to appear in qBittorrent ────────────────────────
log "Polling qBittorrent for new torrent..."
elapsed=0
torrent_found=false

while [[ ${elapsed} -lt ${POLL_TIMEOUT} ]]; do
  torrents="$(qbt_login_and_call "/torrents/info" 2>/dev/null || echo "[]")"

  QBT_HASHES="$(python3 - "${torrents}" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
hashes = []
for t in data:
    name = (t.get("name") or "").lower()
    # Match "matrix" in torrent name (flexible matching)
    if "matrix" in name:
        hashes.append(t["hash"])
print("|".join(hashes))
PY
)" || QBT_HASHES=""

  if [[ -n "${QBT_HASHES}" ]]; then
    torrent_found=true
    log "  Torrent found in qBittorrent: ${QBT_HASHES}"
    break
  fi

  sleep "${POLL_INTERVAL}"
  elapsed=$((elapsed + POLL_INTERVAL))
done

if [[ "${torrent_found}" != "true" ]]; then
  # Dump all torrents for debugging
  log "  Current qBittorrent torrents:"
  qbt_login_and_call "/torrents/info" 2>/dev/null | python3 -c "
import json,sys
data = json.loads(sys.stdin.read())
for t in data:
    print(f'    {t.get(\"name\",\"?\")} category={t.get(\"category\",\"\")} hash={t.get(\"hash\",\"\")}')
if not data:
    print('    (none)')
" 2>/dev/null || true
  fail "Torrent not found in qBittorrent within ${POLL_TIMEOUT}s"
fi

# ── Cleanup ──────────────────────────────────────────────────────────────────
cleanup

log "PASS: Full LLM pipeline verified (openclaw-gateway -> media-agent -> Prowlarr -> qBittorrent)"
exit 0
