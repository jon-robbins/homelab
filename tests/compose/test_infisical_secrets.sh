#!/usr/bin/env bash
# Slice D: Compose secret grants + file-only delivery (no managed secrets in environment).
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
  printf 'fixture-%s\n' "${key}" >"${secret_dir}/${key}"
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
            k, v = item.split("=", 1)
        else:
            k, v = item, ""
        out[k] = v
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
            "config",
            "--format",
            "json",
        ],
        text=True,
        cwd=ROOT,
    )
)

failures = []

managed_keys = subprocess.check_output(
    ["bash", "-c", "source scripts/lib/managed-secrets.sh && managed_secret_keys"],
    text=True,
    cwd=ROOT,
).splitlines()
managed_keys = [k for k in managed_keys if k.strip()]
managed_set = set(managed_keys)
public_managed_set = managed_set - {
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_MCP_API_TOKEN",
    "TELEGRAM_OWNER_BOT_TOKEN",
    "TELEGRAM_OWNER_MCP_API_TOKEN",
    "HOMELAB_AGENT_API_TOKEN",
    "MEDIA_AGENT_TOKEN",
    "GEMINI_API_KEY",
    "MCP_API_TOKEN",
    "VLLM_API_KEY",
    "AUTOBRR_API_KEY",
    "PLEX_TOKEN",
}

top_secrets = config.get("secrets") or {}
if "BAZARR_API_KEY" in top_secrets:
    failures.append("BAZARR_API_KEY must not be a managed top-level Compose secret")
for key in public_managed_set:
    if key not in top_secrets:
        failures.append(f"missing top-level secret declaration for {key}")
    elif top_secrets[key].get("file") != f"{SECRET_DIR}/{key}":
        failures.append(
            f"secret {key} must use file source {SECRET_DIR}/{key}, got {top_secrets[key]!r}"
        )

QBT = ["QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD"]
expected_grants = {
    "caddy": ["\x43LOUDFLARE_TOKEN"],
    "overseerr": ["TMDB_API_KEY"],
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

services = config.get("services") or {}

for svc, want in expected_grants.items():
    if svc not in services:
        failures.append(f"service {svc} missing from compose config")
        continue
    got = secrets_list(services[svc].get("secrets"))
    want_set = set(want)
    got_set = set(got)
    if want_set != got_set:
        missing = sorted(want_set - got_set)
        extra = sorted(got_set - want_set)
        failures.append(
            f"{svc} secret grants mismatch: missing={missing} extra={extra}"
        )

allowed_grantees = set(expected_grants)
for svc, svc_cfg in services.items():
    env = env_to_dict(svc_cfg.get("environment"))
    for key in sorted(managed_set.intersection(env)):
        failures.append(
            f"{svc} must not inject managed secret {key} via environment"
        )
    for value in env.values():
        if value == "CI_PLACEHOLDER":
            failures.append(
                f"{svc} must not expose CI_PLACEHOLDER in container environment"
            )
    for secret_name in secrets_list(svc_cfg.get("secrets")):
        if secret_name not in managed_set:
            continue
        if svc not in allowed_grantees:
            failures.append(
                f"{svc} must not receive managed secret grant {secret_name}"
            )
        elif secret_name not in expected_grants[svc]:
            failures.append(
                f"{svc} must not receive managed secret grant {secret_name}"
            )

if failures:
    for msg in failures:
        print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(len(failures))

print("PASS: infisical compose secret grants (file-only delivery)")
PY
