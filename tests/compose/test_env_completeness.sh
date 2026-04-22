#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

if [[ ! -f ".env.example" ]]; then
  echo "FAIL: .env.example not found"
  exit 1
fi

COMPOSE_FILES=(
  "docker-compose.network.yml"
  "docker-compose.media.yml"
  "docker-compose.llm.yml"
)

[[ -f "docker-compose.gpu.yml" ]] && COMPOSE_FILES+=("docker-compose.gpu.yml")
[[ -f "config/gpu/docker-compose.gpu.yml" ]] && COMPOSE_FILES+=("config/gpu/docker-compose.gpu.yml")

declare -A all_vars=()
declare -A vars_with_defaults=()
declare -A env_keys=()

while IFS= read -r token; do
  inner="${token#\$\{}"
  inner="${inner%\}}"

  if [[ "$inner" =~ ^([A-Za-z_][A-Za-z0-9_]*)(:-.*)?$ ]]; then
    var_name="${BASH_REMATCH[1]}"
    all_vars["$var_name"]=1
    if [[ -n "${BASH_REMATCH[2]:-}" ]]; then
      vars_with_defaults["$var_name"]=1
    fi
  fi
done < <(grep -hoE '\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^}]*)?\}' "${COMPOSE_FILES[@]}" 2>/dev/null | sort -u)

while IFS= read -r key; do
  [[ -n "$key" ]] && env_keys["$key"]=1
done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env.example | cut -d= -f1 | sort -u)

missing=()
while IFS= read -r var_name; do
  [[ -z "$var_name" ]] && continue
  if [[ -n "${env_keys[$var_name]:-}" ]]; then
    continue
  fi
  if [[ -n "${vars_with_defaults[$var_name]:-}" ]]; then
    continue
  fi
  missing+=("$var_name")
done < <(printf '%s\n' "${!all_vars[@]}" | sort -u)

if (( ${#missing[@]} > 0 )); then
  echo "FAIL: Variables missing from .env.example:"
  for var_name in "${missing[@]}"; do
    echo "  - ${var_name}"
  done
  exit 1
fi

echo "PASS: All compose variables are defined in .env.example or provide inline defaults"
