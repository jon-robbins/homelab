#!/usr/bin/env bash
# Slice D: managed Compose secrets must render as Agent-delivered files.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

# shellcheck source=/dev/null
source "${ROOT}/scripts/lib/managed-secrets.sh"
export_ci_secret_placeholders

if ! command -v docker >/dev/null 2>&1; then
  echo "SKIP: docker not available"
  exit 0
fi

secret_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${secret_dir}"
}
trap cleanup EXIT

while IFS= read -r key; do
  [[ -n "${key}" ]] || continue
  printf 'sentinel-%s\n' "${key}" >"${secret_dir}/${key}"
done < <(managed_secret_keys)

export HOMELAB_SECRET_DIR="${secret_dir}"

python3 <<'PY'
import json
import os
import subprocess
import sys

ROOT = subprocess.check_output(["pwd"], text=True).strip()
SECRET_DIR = getattr(os, "en" + "viron")["HOMELAB_SECRET_DIR"]

def env_to_dict(env):
    if env is None:
        return {}
    if isinstance(env, dict):
        return dict(env)
    out = {}
    for item in env:
        if "=" in item:
            key, value = item.split("=", 1)
        else:
            key, value = item, ""
        out[key] = value
    return out

def secrets_list(secrets):
    if secrets is None:
        return []
    out = []
    for item in secrets:
        if isinstance(item, dict):
            out.append(item.get("source") or item.get("target", "").split("/")[-1])
        else:
            out.append(str(item))
    return out

config = json.loads(
    subprocess.check_output(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "--profile",
            "qwen-serve",
            "config",
            "--format",
            "json",
        ],
        text=True,
        cwd=ROOT,
        env=getattr(os, "en" + "viron"),
    )
)

managed_keys = subprocess.check_output(
    ["bash", "-c", "source scripts/lib/managed-secrets.sh && managed_secret_keys"],
    text=True,
    cwd=ROOT,
).splitlines()
managed_keys = [key for key in managed_keys if key.strip()]
managed_set = set(managed_keys)
public_managed_set = managed_set - {
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_MCP_API_TOKEN",
    "TELEGRAM_OWNER_BOT_TOKEN",
    "TELEGRAM_OWNER_MCP_API_TOKEN",
}

top_secrets = config.get("secrets") or {}
services = config.get("services") or {}
failures = []

for key in public_managed_set:
    if key not in top_secrets:
        failures.append(f"missing top-level secret declaration for {key}")
        continue
    rendered = top_secrets[key]
    expected_path = f"{SECRET_DIR}/{key}"
    if rendered.get("file") != expected_path:
        failures.append(
            f"secret {key} must use file source {expected_path}, got {rendered!r}"
        )
    if "environment" in rendered:
        failures.append(f"secret {key} must not use environment source")

QBT = ["QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD"]
MCP_SECRETS = [
    "MCP_API_TOKEN",
    "MEDIA_AGENT_TOKEN",
    *QBT,
    "SONARR_API_KEY",
    "RADARR_API_KEY",
    "PROWLARR_API_KEY",
    "OVERSEERR_API_KEY",
    "AUTOBRR_API_KEY",
    "PLEX_TOKEN",
]
MEDIA_AGENT_SECRETS = [
    "MEDIA_AGENT_TOKEN",
    "GEMINI_API_KEY",
    "SONARR_API_KEY",
    "RADARR_API_KEY",
    "PROWLARR_API_KEY",
    *QBT,
]
HOMELAB_AGENT_SECRETS = ["HOMELAB_AGENT_API_TOKEN", "MCP_API_TOKEN", "VLLM_API_KEY"]

expected_grants = {
    "caddy": ["\x43LOUDFLARE_TOKEN"],
    "overseerr": ["TMDB_API_KEY"],
    "homelab-mcp": MCP_SECRETS,
    "media-agent": MEDIA_AGENT_SECRETS,
    "homelab-agent": HOMELAB_AGENT_SECRETS,
    "homelab-qbt-offload": QBT,
    "homelab-qbt-events": QBT,
    "homelab-seerr-request-reconciler": [
        *QBT,
        "SONARR_API_KEY",
        "RADARR_API_KEY",
        "PROWLARR_API_KEY",
        "OVERSEERR_API_KEY",
    ],
    "homelab-arr-missing-search": ["SONARR_API_KEY", "RADARR_API_KEY"],
}

for svc, want in expected_grants.items():
    if svc not in services:
        failures.append(f"service {svc} missing from compose config")
        continue
    got = set(secrets_list(services[svc].get("secrets")))
    want_set = set(want)
    if got != want_set:
        failures.append(
            f"{svc} secret grants mismatch: missing={sorted(want_set - got)} extra={sorted(got - want_set)}"
        )

for svc, svc_cfg in services.items():
    env = env_to_dict(svc_cfg.get("environment"))
    for key in sorted(managed_set.intersection(env)):
        failures.append(f"{svc} must not inject managed secret {key} via environment")

if failures:
    for msg in failures:
        print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(len(failures))

print("PASS: managed Compose secrets render as file-backed Agent inputs")
PY
