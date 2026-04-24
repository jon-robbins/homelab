#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PKG_DIR="${REPO_ROOT}/src/homelab_workers"
PKG_VENV="${PKG_DIR}/.venv"
MEDIA_AGENT_DIR="${REPO_ROOT}/media-agent"
DEBUG_LOG_PATH="${REPO_ROOT}/.cursor/debug-f44d4c.log"

declare -i TOTAL=0
declare -i PASSED=0
declare -i FAILED=0

log() {
  printf '[ci-local] %s\n' "$*"
}

docker_available() {
  command -v docker >/dev/null 2>&1
}

debug_log() {
  local hypothesis_id="$1"
  local message="$2"
  local data="$3"
  python3 - "$hypothesis_id" "$message" "$data" "$DEBUG_LOG_PATH" <<'PY'
import json
import sys
import time
from pathlib import Path

hypothesis_id, message, data, path = sys.argv[1:5]
payload = {
    "sessionId": "f44d4c",
    "runId": "ci-local-debug",
    "hypothesisId": hypothesis_id,
    "location": "scripts/ci-local.sh",
    "message": message,
    "data": {"raw": data},
    "timestamp": int(time.time() * 1000),
}
Path(path).parent.mkdir(parents=True, exist_ok=True)
with Path(path).open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
PY
}

run_step() {
  local name="$1"
  shift
  ((TOTAL += 1))
  # #region agent log
  debug_log "H1" "run_step_start" "step=${name}"
  # #endregion
  log "START ${name}"
  if "$@"; then
    ((PASSED += 1))
    # #region agent log
    debug_log "H2" "run_step_pass" "step=${name}"
    # #endregion
    log "PASS  ${name}"
  else
    ((FAILED += 1))
    # #region agent log
    debug_log "H3" "run_step_fail" "step=${name}"
    # #endregion
    log "FAIL  ${name}"
  fi
}

ensure_pkg_venv() {
  if [[ -x "${PKG_VENV}/bin/python" ]]; then
    # #region agent log
    debug_log "H4" "pkg_venv_exists" "venv=${PKG_VENV}"
    # #endregion
    return 0
  fi

  log "Create package venv at ${PKG_VENV}"
  # #region agent log
  debug_log "H4" "pkg_venv_create" "venv=${PKG_VENV}"
  # #endregion
  python3 -m venv "${PKG_VENV}"
  "${PKG_VENV}/bin/pip" install -e "${PKG_DIR}[dev]"
}

ensure_scripts_test_deps() {
  if "${PKG_VENV}/bin/python" -c "import pydantic" >/dev/null 2>&1; then
    # #region agent log
    debug_log "H6" "scripts_deps_present" "module=pydantic"
    # #endregion
    return 0
  fi
  # #region agent log
  debug_log "H6" "scripts_deps_install" "module=pydantic"
  # #endregion
  "${PKG_VENV}/bin/pip" install pydantic
}

ensure_media_agent_test_deps() {
  if [[ ! -d "${MEDIA_AGENT_DIR}" ]]; then
    return 0
  fi
  if "${PKG_VENV}/bin/python" -c "import fastapi, respx" >/dev/null 2>&1; then
    return 0
  fi
  "${PKG_VENV}/bin/pip" install -r "${MEDIA_AGENT_DIR}/requirements-dev.txt"
}

check_compose_configs() {
  if ! docker_available; then
    log "SKIP compose config checks (docker is not installed)"
    return 0
  fi
  docker compose -f "${REPO_ROOT}/docker-compose.network.yml" config --quiet
  docker compose -f "${REPO_ROOT}/docker-compose.media.yml" config --quiet
  docker compose -f "${REPO_ROOT}/docker-compose.llm.yml" config --quiet
}

check_optional_gpu_overlay() {
  if ! docker_available; then
    log "SKIP GPU overlay compose check (docker is not installed)"
    return 0
  fi
  local gpu_root="${REPO_ROOT}/docker-compose.gpu.yml"
  local gpu_template="${REPO_ROOT}/config/gpu/docker-compose.gpu.yml"
  if [[ -f "${gpu_root}" ]]; then
    docker compose \
      -f "${REPO_ROOT}/docker-compose.network.yml" \
      -f "${REPO_ROOT}/docker-compose.media.yml" \
      -f "${REPO_ROOT}/docker-compose.llm.yml" \
      -f "${gpu_root}" \
      config --quiet
    return 0
  fi

  if [[ -f "${gpu_template}" ]]; then
    docker compose \
      -f "${REPO_ROOT}/docker-compose.network.yml" \
      -f "${REPO_ROOT}/docker-compose.media.yml" \
      -f "${REPO_ROOT}/docker-compose.llm.yml" \
      -f "${gpu_template}" \
      config --quiet
  fi
}

check_bash_syntax() {
  local files=(
    "${REPO_ROOT}/scripts/setup.sh"
    "${REPO_ROOT}/tests/compose/run_tests.sh"
    "${REPO_ROOT}/tests/runtime/run_tests.sh"
  )
  local script
  for script in "${files[@]}"; do
    [[ -f "${script}" ]] && bash -n "${script}"
  done
}

run_runtime_smoke_tests() {
  if [[ "${CI_LOCAL_RUNTIME_SMOKE:-0}" != "1" ]]; then
    log "SKIP runtime smoke tests (set CI_LOCAL_RUNTIME_SMOKE=1 to enable)"
    return 0
  fi

  bash "${REPO_ROOT}/tests/runtime/run_tests.sh"
}

run_llm_behavior_check() {
  if [[ "${CI_LOCAL_LLM_BEHAVIOR:-1}" != "1" ]]; then
    log "SKIP llm behavior check (set CI_LOCAL_LLM_BEHAVIOR=1 to enable)"
    # #region agent log
    debug_log "H7" "llm_behavior_skip" "enabled=${CI_LOCAL_LLM_BEHAVIOR:-unset}"
    # #endregion
    return 0
  fi
  if ! docker_available; then
    log "SKIP llm behavior check (docker is not installed)"
    return 0
  fi
  # #region agent log
  debug_log "H7" "llm_behavior_run" "max_attempts=${CI_LOCAL_LLM_MAX_ATTEMPTS:-1}"
  # #endregion
  DEBUG_RUN="ci-local-llm" DEBUG_MAX_ATTEMPTS="${CI_LOCAL_LLM_MAX_ATTEMPTS:-1}" \
    python3 "${REPO_ROOT}/scripts/debug_openclaw_media_llm.py"
}

run_llm_download_flow_check() {
  if [[ "${CI_LOCAL_LLM_DOWNLOAD_FLOW:-1}" != "1" ]]; then
    log "SKIP llm download-flow check (set CI_LOCAL_LLM_DOWNLOAD_FLOW=1 to enable)"
    # #region agent log
    debug_log "H8" "llm_download_flow_skip" "enabled=${CI_LOCAL_LLM_DOWNLOAD_FLOW:-unset}"
    # #endregion
    return 0
  fi
  if ! docker_available; then
    log "SKIP llm download-flow check (docker is not installed)"
    return 0
  fi
  local request_text="${CI_LOCAL_LLM_DOWNLOAD_REQUEST:-Download Crazy Ex Girlfriend season 4}"
  local session_key
  session_key="ci-download-$(date +%s)"
  # #region agent log
  debug_log "H8" "llm_download_flow_start" "session_key=${session_key},request=${request_text}"
  # #endregion
  local out
  out="$(
    docker exec -e ROUTER_SESSION_KEY="${session_key}" -e ROUTER_REQUEST_TEXT="${request_text}" openclaw-gateway sh -lc 'node -e "
      (async()=>{
        const base=process.env.MEDIA_AGENT_URL;
        const tok=process.env.MEDIA_AGENT_TOKEN;
        const h={\"Authorization\":\"Bearer \"+tok,\"Content-Type\":\"application/json\"};
        const session=process.env.ROUTER_SESSION_KEY || \"\";
        const requestText=process.env.ROUTER_REQUEST_TEXT || \"Download Crazy Ex Girlfriend season 4\";
        const first=await fetch(base+\"/internal/media-agent/v1/router\",{method:\"POST\",headers:h,body:JSON.stringify({message:requestText,session_key:session})});
        const j1=await first.json();
        const options=((j1.tool_result||{}).options)||[];
        const cap=Math.min(5, options.length || 0);
        const pick=cap > 0 ? (1 + Math.floor(Math.random()*cap)) : null;
        const picked=(pick!==null?options[pick-1]:null)||{};
        const pickedHash=(picked.release&&picked.release.infoHash)||\"\";
        let secondStatus=null;
        let j2={};
        if(pick !== null){
          const second=await fetch(base+\"/internal/media-agent/v1/router\",{method:\"POST\",headers:h,body:JSON.stringify({message:String(pick),session_key:session})});
          secondStatus=second.status;
          j2=await second.json();
        }
        console.log(JSON.stringify({
          session_len: session.length,
          request_text: requestText,
          pick,
          first_hash: pickedHash,
          first:{status:first.status,intent:j1.intent&&j1.intent.intent,action:j1.action&&j1.action.action,tool_ok:j1.tool_result&&j1.tool_result.ok,options_count:options.length,response:j1.response_text},
          second:{status:secondStatus,intent:j2.intent&&j2.intent.intent,action:j2.action&&j2.action.action,tool_ok:j2.tool_result&&j2.tool_result.ok,response:j2.response_text,error:j2.error}
        }));
      })().catch(e=>{console.error(e.message);process.exit(1);});
    "'
  )"
  # #region agent log
  debug_log "H9" "llm_download_flow_result" "payload=${out:0:500}"
  # #endregion
  local rc=0
  python3 - "$out" <<'PY' || rc=$?
import json
import sys

obj = json.loads(sys.argv[1])
second = obj.get("second") or {}
ok = second.get("tool_ok") is True
resp = str(second.get("response") or "")
reuse_ok = "already in qBittorrent" in resp and "reused it" in resp
if ok and (resp.startswith("OK! It's downloading.") or reuse_ok):
    sys.exit(0)
print(json.dumps(obj, indent=2))
err = second.get("error") or {}
code = str(err.get("code") or "")
msg = str(err.get("message") or "")
if code == "GRAB_FAILED" and "HTTP 500" in msg:
    sys.exit(10)
sys.exit(1)
PY
  if [[ "${rc}" -eq 0 ]]; then
    return 0
  fi
  if [[ "${rc}" -eq 10 ]]; then
    local reason="grab_failed_http_500"
    local first_hash=""
    first_hash="$(python3 - "$out" <<'PY'
import json, sys
try:
    obj = json.loads(sys.argv[1])
except Exception:
    print("")
    raise SystemExit(0)
print((obj.get("first_hash") or "").strip())
PY
)"
    if [[ -n "${first_hash}" ]]; then
      local exists_out=""
      exists_out="$(
        set -a
        # shellcheck source=/dev/null
        source "${REPO_ROOT}/.env"
        set +a
        docker exec -e QB_USER="${QBITTORRENT_USERNAME:-}" -e QB_PASS="${QBITTORRENT_PASSWORD:-}" -e QHASH="${first_hash}" prowlarr sh -lc '
          QB_URL=http://qbittorrent:8080
          COOKIE=/tmp/qb_cookie_ci.txt
          rm -f "$COOKIE"
          if [ -z "$QB_USER" ] || [ -z "$QB_PASS" ]; then
            echo "no-creds"
            exit 0
          fi
          curl -sS -c "$COOKIE" -X POST --data-urlencode "username=${QB_USER}" --data-urlencode "password=${QB_PASS}" "$QB_URL/api/v2/auth/login" >/dev/null || { echo "login-fail"; exit 0; }
          curl -sS -b "$COOKIE" "$QB_URL/api/v2/torrents/info?hashes=${QHASH}" | wc -c
        ' 2>/dev/null || true
      )"
      if [[ "${exists_out}" =~ ^[[:space:]]*[1-9][0-9]*[[:space:]]*$ ]]; then
        reason="already_present_in_qbittorrent"
        echo "[ci-local] LLM download flow note: selected torrent already exists in qBittorrent queue."
        # #region agent log
        debug_log "H11" "llm_download_flow_already_present" "hash=${first_hash}"
        # #endregion
        return 0
      fi
    fi
    local prowlarr_tail
    prowlarr_tail="$(docker logs --tail 200 prowlarr 2>&1 || true)"
    if [[ "${prowlarr_tail}" == *"Download client isn't configured yet"* ]]; then
      reason="prowlarr_download_client_not_configured"
      echo "[ci-local] LLM download flow failed: Prowlarr has no download client configured."
    elif [[ "${prowlarr_tail}" == *"Download client failed to add torrent by url"* ]]; then
      reason="prowlarr_download_client_add_url_failed"
      echo "[ci-local] LLM download flow failed: Prowlarr could not add torrent URL to qBittorrent."
    fi
    # #region agent log
    debug_log "H10" "llm_download_flow_failure_reason" "reason=${reason}"
    # #endregion
    return 1
  fi
  return "${rc}"
}

run_router_smoke_p0_gate() {
  if ! docker_available; then
    log "SKIP router smoke P0 gate (docker is not installed)"
    return 0
  fi
  local out
  out="$(
    docker exec -i media-agent python - <<'PY'
import json
import os
import urllib.error
import urllib.request

base = "http://127.0.0.1:8000/internal/media-agent/v1"
auth = {"Authorization": "Bearer " + os.environ["MEDIA_AGENT_TOKEN"]}
headers = {**auth, "Content-Type": "application/json"}

def post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers=headers,
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=180).read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() or "{}"
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": {"code": "HTTP_ERROR", "message": body}}

def get_json(path: str) -> dict:
    req = urllib.request.Request(base + path, headers=auth)
    return json.loads(urllib.request.urlopen(req, timeout=180).read().decode())

gate = get_json("/router-smoke-gate")
steps = gate.get("steps") or []
expect = gate.get("expectations") or {}
session_key = str(gate.get("session_key") or "smoke-cxg-s4")
if len(steps) < 2:
    raise SystemExit("router-smoke-gate returned insufficient steps")

first = post_json("/router", steps[0])
first_tool = first.get("tool_result") or {}
first_reuse_ok = (
    first_tool.get("existing_torrent_reused") is True
    and str(first_tool.get("status") or "") in {"enabled", "already_downloaded"}
)
second = post_json("/router", steps[1]) if not first_reuse_ok else {}
second_tool = second.get("tool_result") or {}
season_selection = second_tool.get("season_selection") or {}
second_ok = (
    second_tool.get("ok") is True
    and season_selection.get("status") == expect.get("season_only_status")
)
ok = first_reuse_ok or second_ok
print(json.dumps({
    "session_key": session_key,
    "first": {
        "action": (first.get("action") or {}).get("action"),
        "tool_ok": first_tool.get("ok"),
        "reuse": first_tool.get("existing_torrent_reused"),
        "reuse_status": first_tool.get("status"),
        "response": first.get("response_text"),
    },
    "second": {
        "action": (second.get("action") or {}).get("action"),
        "response": second.get("response_text"),
        "tool_ok": second_tool.get("ok"),
        "season_selection": season_selection,
        "error": second_tool.get("error") or second.get("error"),
    },
    "ok": ok,
}))
PY
  )"
  python3 - "$out" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj.get("ok") is True:
    raise SystemExit(0)
print(json.dumps(obj, indent=2))
raise SystemExit(1)
PY
}

check_mermaid_blocks() {
  python3 - <<'PY'
from pathlib import Path
import re
import sys

readme = Path("README.md")
if not readme.exists():
    print("README.md missing", file=sys.stderr)
    sys.exit(1)

content = readme.read_text(encoding="utf-8")
count = len(re.findall(r"```mermaid", content))
if count < 1:
    print("README must contain at least one mermaid block", file=sys.stderr)
    sys.exit(1)
PY
}

main() {
  cd "${REPO_ROOT}"

  run_step "compose-shell-tests" bash "${REPO_ROOT}/tests/compose/run_tests.sh"
  run_step "compose-config" check_compose_configs
  run_step "compose-gpu-overlay" check_optional_gpu_overlay
  ensure_pkg_venv
  ensure_scripts_test_deps
  ensure_media_agent_test_deps
  run_step "scripts-pytest" "${PKG_VENV}/bin/python" -m pytest -q "${REPO_ROOT}/scripts/tests"
  run_step "media-agent-pytest" "${PKG_VENV}/bin/python" -m pytest -q "${MEDIA_AGENT_DIR}/tests"
  run_step "scripts-ruff" "${PKG_VENV}/bin/ruff" check --ignore E402 "${REPO_ROOT}/scripts/workers"
  run_step "package-pytest" "${PKG_VENV}/bin/pytest" -q "${PKG_DIR}"
  run_step "package-ruff" "${PKG_VENV}/bin/ruff" check "${PKG_DIR}/src"
  run_step "bash-syntax" check_bash_syntax
  run_step "docs-mermaid" check_mermaid_blocks
  run_step "llm-behavior" run_llm_behavior_check
  run_step "llm-download-flow" run_llm_download_flow_check
  run_step "router-smoke-p0" run_router_smoke_p0_gate
  run_step "runtime-smoke" run_runtime_smoke_tests

  log "DONE total=${TOTAL} passed=${PASSED} failed=${FAILED}"
  # #region agent log
  debug_log "H5" "suite_summary" "total=${TOTAL},passed=${PASSED},failed=${FAILED}"
  # #endregion
  if ((FAILED > 0)); then
    exit 1
  fi
}

main "$@"
