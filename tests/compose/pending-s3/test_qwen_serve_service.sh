#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

fail() { echo "FAIL: $*" >&2; exit 1; }

grep -qE '^[[:space:]]*qwen-serve:' compose/llm.yml \
  || fail "qwen-serve service missing from compose/llm.yml"

block="$(awk '/^  qwen-serve:/{f=1;next} f && /^  [a-z]/{f=0} f' compose/llm.yml)"

# External access: loopback + Tailscale IP only (Trae via tailnet, qwen-chat
# and probes via localhost). Never 0.0.0.0 — Docker-published ports bypass UFW.
grep -qE '^[[:space:]]*-[[:space:]]*"127\.0\.0\.1:18020:18020"' <<<"$block" \
  || fail "qwen-serve must publish 127.0.0.1:18020 for host-local clients"
grep -qE '^[[:space:]]*-[[:space:]]*"\$\{QWEN_SERVE_TAILSCALE_IP:-100\.106\.194\.32\}:18020:18020"' <<<"$block" \
  || fail "qwen-serve must publish the Tailscale IP for tailnet clients (Trae)"
if grep -qE '^[[:space:]]*-[[:space:]]*"(0\.0\.0\.0:)?18020:18020"' <<<"$block"; then
  fail "qwen-serve must not bind 0.0.0.0 / all interfaces"
fi

# Entrypoint must preflight model readability under cap_drop:ALL (a
# mode-600 shard aborted the engine in Stage A.5 with a misleading
# FileNotFoundError from safetensors).
grep -q '! -readable' "${REPO_ROOT}/apps/qwen-serve/entrypoint.sh" \
  || fail "entrypoint must verify model files are readable (cap_drop:ALL preflight)"
grep -q 'VLLM_API_KEY' <<<"$block" \
  || fail "qwen-serve missing VLLM_API_KEY secret grant"
grep -q 'shm_size' <<<"$block" \
  || fail "qwen-serve missing shm_size"
grep -qE '^[[:space:]]*gpus: all' <<<"$block" \
  || fail "qwen-serve missing explicit GPU request (gpus: all)"
grep -q 'enable-auto-tool-choice' <<<"$block" \
  || fail "qwen-serve command missing --enable-auto-tool-choice"
grep -q 'tool-call-parser' <<<"$block" \
  || fail "qwen-serve command missing --tool-call-parser"
grep -q 'healthcheck' <<<"$block" \
  || fail "qwen-serve missing healthcheck"
grep -q 'qwen/models:/models:ro' <<<"$block" \
  || fail "qwen-serve must mount the model dir read-only at /models"
grep -q 'kv-cache-dtype kvarn_k4v2_g128' <<<"$block" \
  || fail "qwen-serve command missing KVarN cache dtype"
grep -q 'max-model-len 262144' <<<"$block" \
  || fail "qwen-serve command missing --max-model-len 262144"
grep -q 'profiles: \["qwen-serve"\]' <<<"$block" \
  || fail "qwen-serve must be profile-gated (unscoped nightly must not start it)"
grep -q 'no-new-privileges:true' <<<"$block" \
  || fail "qwen-serve missing security_opt no-new-privileges"
grep -q 'cap_drop: \[ALL\]' <<<"$block" \
  || fail "qwen-serve missing cap_drop: [ALL]"

echo "PASS: qwen-serve compose wiring"
