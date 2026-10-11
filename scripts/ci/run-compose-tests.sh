#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

local_run=0
requested_tests=()
for argument in "$@"; do
  if [[ "$argument" == "--local" ]]; then
    local_run=1
  else
    requested_tests+=("$argument")
  fi
done

tests_to_run=()
if (( local_run == 1 && ${#requested_tests[@]} == 0 )); then
  echo "SKIP: test_host_ports_available.sh (production-host preflight)"
  echo "SKIP: test_yaml_syntax.sh (requires a Docker daemon)"
  test_list="$(bash "${ROOT}/tests/compose/run_tests.sh" --list)"
  while IFS= read -r test_script; do
    case "$test_script" in
      test_host_ports_available.sh|test_yaml_syntax.sh) ;;
      *) tests_to_run+=("$test_script") ;;
    esac
  done <<< "$test_list"
else
  for test_script in "${requested_tests[@]}"; do
    if (( local_run == 1 )) && [[ "$test_script" == "test_host_ports_available.sh" ]]; then
      echo "SKIP: test_host_ports_available.sh (production-host preflight)"
      continue
    fi
    if (( local_run == 1 )) && [[ "$test_script" == "test_yaml_syntax.sh" ]]; then
      echo "SKIP: test_yaml_syntax.sh (requires a Docker daemon)"
      continue
    fi
    tests_to_run+=("$test_script")
  done
fi

if (( local_run == 0 && ${#requested_tests[@]} == 0 )); then
  run_all=1
else
  run_all=0
fi
unset SKIP_HOST_PORT_PREFLIGHT

# Use the public contract for local renders, matching the documented checkout.
if [[ -f "${ROOT}/.env.example" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT}/.env.example"
  set +a
fi

# shellcheck source=/dev/null
source "${ROOT}/scripts/ci/export-ci-secret-placeholders.sh"
secret_dir="$(mktemp -d)"
trap 'rm -rf "${secret_dir}"' EXIT
export HOMELAB_SECRET_DIR="$secret_dir"
while IFS= read -r key; do
  [[ -n "$key" ]] || continue
  printf 'CI_SENTINEL_%s\n' "$key" >"${HOMELAB_SECRET_DIR}/${key}"
done < <(managed_secret_keys)

if (( run_all == 1 )); then
  bash "${ROOT}/tests/compose/run_tests.sh"
elif (( ${#tests_to_run[@]} > 0 )); then
  bash "${ROOT}/tests/compose/run_tests.sh" "${tests_to_run[@]}"
fi
