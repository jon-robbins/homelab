#!/usr/bin/env bash
# Canonical Phase-1 managed secrets (sourced by ops/CI/tests). Never print values.

_MANAGED_SECRETS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_MANAGED_SECRETS_PY="${_MANAGED_SECRETS_LIB_DIR}/managed_secrets.py"

managed_secret_keys() {
  python3 "${_MANAGED_SECRETS_PY}" keys
}

managed_secret_key_path_pairs() {
  python3 "${_MANAGED_SECRETS_PY}" pairs
}

managed_secret_path_for_key() {
  python3 "${_MANAGED_SECRETS_PY}" path-for-key "$1"
}

managed_secret_paths() {
  python3 "${_MANAGED_SECRETS_PY}" paths
}

export_ci_secret_placeholders() {
  local key
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    export "${key}=CI_PLACEHOLDER"
  done < <(managed_secret_keys)
}

# Export non-managed keys from an env file without overriding existing values
# and never applying managed secret keys (so Infisical/env secrets win).
source_nonsecret_env() {
  local env_file="${1:-}"
  local line key val
  local -A managed=()

  [[ -n "$env_file" && -f "$env_file" ]] || return 0

  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    managed["$key"]=1
  done < <(managed_secret_keys)

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      [[ -n "${managed[$key]:-}" ]] && continue
      if [[ -z "${!key+x}" ]]; then
        export "${key}=${val}"
      fi
    fi
  done <"$env_file"
}
