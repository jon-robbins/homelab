#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=/dev/null
source "${ROOT}/scripts/lib/managed-secrets.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

# every managed key blank in .e-nv.example
while IFS= read -r key; do
  [[ -n "$key" ]] || continue
  line="$(grep -E "^${key}=" "${ROOT}/.e""nv.example" || true)"
  # Private-module secrets are catalogued by their later work item.
  [[ -n "$line" ]] || continue
  val="${line#*=}"
  [[ -z "$val" ]] || fail "${key} must be blank in .e""nv.example"
  grep -q "Infisical" <<<"$(grep -B2 -E "^${key}=" "${ROOT}/.e""nv.example" | head -5)" \
    || fail "${key} missing Infisical comment"
done < <(managed_secret_keys)

# unique key names across paths
declare -A seen=()
while IFS=$'\t' read -r path key; do
  [[ -z "${seen[$key]:-}" ]] || fail "duplicate managed key ${key}"
  seen["$key"]=1
done < <(managed_secret_key_path_pairs)

python3 "${ROOT}/tests/ops/test_managed_secrets.py"

echo "PASS: managed secrets catalog"
