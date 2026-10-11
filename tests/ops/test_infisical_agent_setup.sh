#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALLER="${ROOT}/scripts/ops/install-infisical-agent.sh"
UNIT_SOURCE="${ROOT}/config/systemd/homelab-infisical-agent.service"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAKE_CLIENT_ID="test-client-id-123456789"
FAKE_CLIENT_SECRET="PLANTED_CLIENT_SECRET_DO_NOT_PRINT"
WORKTREE_MARKER="/.worktrees/"
TEST_USER="$(id -un)"
TEST_GROUP="$(id -gn)"

assert_no_secret_leak() {
  local text="${1:-}"
  if grep -qF "$FAKE_CLIENT_SECRET" <<<"$text"; then
    fail "output leaked client secret"
  fi
}

setup_fake_infisical() {
  local bin_dir="${TMP}/bin"
  mkdir -p "$bin_dir"
  cat >"${bin_dir}/infisical" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod 0755 "${bin_dir}/infisical"
  printf '%s\n' "${bin_dir}/infisical"
}

write_bootstrap() {
  local path="$1"
  cat >"$path" <<EOF
INFISICAL_CLIENT_ID=${FAKE_CLIENT_ID}
INFISICAL_CLIENT_SECRET=${FAKE_CLIENT_SECRET}
INFISICAL_PROJECT_ID=project-id
INFISICAL_ENV=prod
INFISICAL_API_URL=https://app.infisical.com
EOF
}

mode_of() {
  stat -c '%a' "$1"
}

owner_uid_of() {
  stat -c '%u' "$1"
}

run_installer() {
  local bootstrap_path="$1"
  local fake_binary="$2"
  local out rc=0
  out="$(
    HOMELAB_INFISICAL_BOOTSTRAP="$bootstrap_path" \
    HOMELAB_INFISICAL_AGENT_BIN="$fake_binary" \
    HOMELAB_INFISICAL_AGENT_ETC_DIR="${TMP}/etc/infisical-agent" \
    HOMELAB_INFISICAL_AGENT_SYSTEMD_DIR="${TMP}/etc/systemd/system" \
    HOMELAB_INFISICAL_AGENT_USER="$TEST_USER" \
    HOMELAB_INFISICAL_AGENT_GROUP="$TEST_GROUP" \
    bash "$INSTALLER" \
      --config-source "${ROOT}/config/infisical/agent-config.yaml.example" \
      --secret-dir "${TMP}/runtime-secrets" 2>&1
  )" || rc=$?
  assert_no_secret_leak "$out"
  printf '%s' "$out"
  return "$rc"
}

test_setup_contract() {
  [[ -x "$INSTALLER" ]] || fail "missing executable installer: $INSTALLER"
  [[ -f "$UNIT_SOURCE" ]] || fail "missing unit source: $UNIT_SOURCE"

  local fake_binary bootstrap_path out
  fake_binary="$(setup_fake_infisical)"
  bootstrap_path="${TMP}/infisical.e""nv"
  write_bootstrap "$bootstrap_path"
  chmod 0600 "$bootstrap_path"

  out="$(run_installer "$bootstrap_path" "$fake_binary")" || fail "installer failed: $out"

  local secret_dir="${TMP}/runtime-secrets"
  local client_id_file="${TMP}/etc/infisical-agent/auth/client-id"
  local client_secret_file="${TMP}/etc/infisical-agent/auth/client-secret"
  local config_file="${TMP}/etc/infisical-agent/config.yaml"
  local installed_unit="${TMP}/etc/systemd/system/homelab-infisical-agent.service"

  [[ -d "$secret_dir" ]] || fail "secret dir missing"
  [[ "$(mode_of "$secret_dir")" == "700" ]] || fail "secret dir mode must be 700"
  [[ -f "$client_id_file" ]] || fail "client id file missing"
  [[ -f "$client_secret_file" ]] || fail "client secret file missing"
  [[ "$(mode_of "$client_id_file")" == "600" ]] || fail "client id mode must be 600"
  [[ "$(mode_of "$client_secret_file")" == "600" ]] || fail "client secret mode must be 600"
  [[ "$(cat "$client_id_file")" == "$FAKE_CLIENT_ID" ]] || fail "client id file content mismatch"
  [[ "$(cat "$client_secret_file")" == "$FAKE_CLIENT_SECRET" ]] || fail "client secret file content mismatch"
  [[ -f "$config_file" ]] || fail "config file missing"
  [[ -f "$installed_unit" ]] || fail "installed unit missing"
  [[ "$(mode_of "$config_file")" == "600" ]] || fail "config mode must be 600"
  [[ "$(owner_uid_of "$config_file")" == "$(id -u)" ]] || fail "config owner uid must match invoking user"
  grep -qF "client-id: \"${TMP}/etc/infisical-agent/auth/client-id\"" "$config_file" \
    || fail "config missing client-id path"
  grep -qF "client-secret: \"${TMP}/etc/infisical-agent/auth/client-secret\"" "$config_file" \
    || fail "config missing client-secret path"
  grep -qF "destination-path: ${TMP}/runtime-secr""ets/MEDIA_AGENT_TOKEN" "$config_file" \
    || fail "config missing rewritten secret destination"
  grep -qF "ExecStart=/usr/bin/infisical agent --config /etc/infisical-agent/config.yaml" "$installed_unit" \
    || fail "unit ExecStart mismatch"
  grep -qF "User=${TEST_USER}" "$installed_unit" || fail "unit must run as configured user"
  grep -qF "Group=${TEST_GROUP}" "$installed_unit" || fail "unit must run as configured group"
  grep -qF "After=network-online.target" "$installed_unit" || fail "unit missing After"
  grep -qF "Wants=network-online.target" "$installed_unit" || fail "unit missing Wants"

  if grep -qF "$WORKTREE_MARKER" "$config_file"; then
    fail "config must not contain worktree path"
  fi
  if grep -qF "$WORKTREE_MARKER" "$installed_unit"; then
    fail "unit must not contain worktree path"
  fi
  if grep -qF "$FAKE_CLIENT_SECRET" "$config_file"; then
    fail "config must not contain client secret value"
  fi
  if grep -qF "$FAKE_CLIENT_SECRET" "$installed_unit"; then
    fail "unit must not contain client secret value"
  fi
  grep -qF "systemctl enable --now homelab-infisical-agent.service" <<<"$out" \
    || fail "expected enable instruction in output"
  pass "setup creates secure files and runtime-safe config"
}

test_group_readable_bootstrap_rejected() {
  local fake_binary bootstrap_path out
  fake_binary="$(setup_fake_infisical)"
  bootstrap_path="${TMP}/infisical-group-readable.e""nv"
  write_bootstrap "$bootstrap_path"
  chmod 0640 "$bootstrap_path"

  if out="$(run_installer "$bootstrap_path" "$fake_binary")"; then
    fail "expected group-readable bootstrap rejection"
  fi
  assert_no_secret_leak "$out"
  grep -qF "group/other" <<<"$out" || fail "expected permission rejection message"
  pass "group-readable bootstrap rejected"
}

test_missing_required_field_rejected() {
  local fake_binary bootstrap_path out
  fake_binary="$(setup_fake_infisical)"
  bootstrap_path="${TMP}/infisical-missing-field.e""nv"
  cat >"$bootstrap_path" <<EOF
INFISICAL_CLIENT_ID=${FAKE_CLIENT_ID}
INFISICAL_PROJECT_ID=project-id
EOF
  chmod 0600 "$bootstrap_path"

  if out="$(run_installer "$bootstrap_path" "$fake_binary")"; then
    fail "expected missing field rejection"
  fi
  assert_no_secret_leak "$out"
  grep -qF "INFISICAL_CLIENT_SECRET" <<<"$out" || fail "expected missing-field message"
  pass "missing bootstrap fields rejected"
}

test_setup_contract
test_group_readable_bootstrap_rejected
test_missing_required_field_rejected

echo "PASS: infisical Agent setup contracts"
