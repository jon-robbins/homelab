#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

TEST_SCRIPTS=(
  "test_yaml_syntax.sh"
  "test_env_completeness.sh"
  "test_managed_secrets_catalog.sh"
  "test_infisical_agent_files.sh"
  "test_infisical_secrets.sh"
  "test_no_hardcoded_paths.sh"
  "test_caddy_labels.sh"
  "test_caddy_entrypoint.sh"
  "test_network_definitions.sh"
  "test_app_data_dir_paths.sh"
  "test_host_mode_whitelist.sh"
  "test_host_ports_available.sh"
  "test_arr_missing_search_service.sh"
  "test_autobrr_compose.sh"
  "test_seerr_entrypoint.sh"
  "test_worker_anchor_equivalence.sh"
)

if [[ "${1:-}" == "--list" ]]; then
  if (( $# != 1 )); then
    echo "--list cannot be combined with test names" >&2
    exit 2
  fi
  printf '%s\n' "${TEST_SCRIPTS[@]}"
  exit 0
fi

if (( $# > 0 )); then
  selected_tests=()
  for requested_test in "$@"; do
    case "$requested_test" in
      */*)
        echo "Unknown Compose test: ${requested_test}" >&2
        exit 2
        ;;
    esac
    found=0
    for test_script in "${TEST_SCRIPTS[@]}"; do
      if [[ "$requested_test" == "$test_script" ]]; then
        selected_tests+=("$test_script")
        found=1
        break
      fi
    done
    if (( found == 0 )); then
      echo "Unknown Compose test: ${requested_test}" >&2
      exit 2
    fi
  done
else
  selected_tests=("${TEST_SCRIPTS[@]}")
fi

total=0
passed=0
failed=0

for test_script in "${selected_tests[@]}"; do
  ((total += 1))
  echo "=== Running: ${test_script} ==="
  if bash "${SCRIPT_DIR}/${test_script}"; then
    ((passed += 1))
    echo "RESULT: PASS (${test_script})"
  else
    ((failed += 1))
    echo "RESULT: FAIL (${test_script})"
  fi
  echo ""
done

echo "Compose tests complete: total=${total}, passed=${passed}, failed=${failed}"
if ((failed > 0)); then
  exit 1
fi
