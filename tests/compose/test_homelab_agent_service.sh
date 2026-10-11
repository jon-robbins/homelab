#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

fail() { echo "FAIL: $*" >&2; exit 1; }

grep -qE '^[[:space:]]*homelab-agent:' compose/llm.yml \
  || fail "homelab-agent service missing from compose/llm.yml"
grep -q 'HOMELAB_AGENT_API_TOKEN' compose/base.yml \
  || fail "HOMELAB_AGENT_API_TOKEN secret missing from compose/base.yml"
grep -q '^HOMELAB_AGENT_API_TOKEN=' .e""nv.example \
  || fail "HOMELAB_AGENT_API_TOKEN stub missing from .e""nv.example"
grep -q 'homelab-agent' docs/network-access.md \
  || fail "homelab-agent missing from docs/network-access.md"
# Backend auth: homelab-agent must hold the MCP and vLLM secrets it forwards.
agent_block="$(awk '/^  homelab-agent:/{f=1;next} f && /^  [a-z]/{f=0} f' compose/llm.yml)"
grep -q 'MCP_API_TOKEN' <<<"$agent_block" \
  || fail "homelab-agent missing MCP_API_TOKEN secret grant in compose/llm.yml"
grep -q 'VLLM_API_KEY' <<<"$agent_block" \
  || fail "homelab-agent missing VLLM_API_KEY secret grant in compose/llm.yml"
grep -q 'VLLM_API_KEY' compose/base.yml \
  || fail "VLLM_API_KEY secret missing from compose/base.yml"
grep -q '^VLLM_API_KEY=' .e""nv.example \
  || fail "VLLM_API_KEY stub missing from .e""nv.example"

grep -qF 'HOMELAB_AGENT_MODEL_BASE_URL: ${HOMELAB_AGENT_MODEL_BASE_URL:-http://model:8000/v1}' <<<"$agent_block" \
  || fail "homelab-agent model URL must default to the public model service"

echo "PASS: homelab-agent compose wiring"
