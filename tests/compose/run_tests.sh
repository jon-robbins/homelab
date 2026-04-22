#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

TEST_SCRIPTS=(
  "test_yaml_syntax.sh"
  "test_env_completeness.sh"
  "test_no_hardcoded_paths.sh"
  "test_caddy_labels.sh"
  "test_network_definitions.sh"
  "test_host_mode_whitelist.sh"
)

total=0
passed=0
failed=0

for test_script in "${TEST_SCRIPTS[@]}"; do
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
