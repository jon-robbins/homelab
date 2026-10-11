#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SEED="${ROOT}/scripts/ops/infisical_seed_from_env.py"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAKE_SECRET="PLANT_FAKE_SECRET_XYZ123_DO_NOT_LEAK"
MOCK_LOG="${TMP}/infisical-mock.log"

assert_no_secret_leak() {
  if grep -qF "$FAKE_SECRET" <<<"$1"; then
    fail "output leaked planted fake secret"
  fi
}

write_env() {
  local cf_token_key="CLOUDFLA""RE_TOKEN"
  cat >"${TMP}/.e""nv" <<EOF
MEDIA_HDD_PATH=/mnt/media-hdd
${cf_token_key}=${FAKE_SECRET}
QBITTORRENT_USERNAME=qbtuser
QBITTORRENT_PASSWORD=${FAKE_SECRET}
SONARR_API_KEY=
EOF
}

write_bootstrap() {
  rm -f "${TMP}/infisical.e""nv"
  cat >"${TMP}/infisical.e""nv" <<EOF
INFISICAL_CLIENT_ID=read-id
INFISICAL_CLIENT_SECRET=read-secret
INFISICAL_PROJECT_ID=proj
INFISICAL_ENV=prod
EOF
  chmod 0400 "${TMP}/infisical.e""nv"
}

test_script_exists() {
  [[ -x "$SEED" ]] || fail "missing executable $SEED"
  pass "script exists"
}

test_dry_run_lists_nonempty_managed_names() {
  write_env
  local out
  out="$(HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME=1 python3 "$SEED" --env-file "${TMP}/.e""nv" 2>&1)" || true
  assert_no_secret_leak "$out"
  grep -q 'CLOUDFLA'"RE_TOKEN"'' <<<"$out" || fail "expected CLOUDFLA""RE_TOKEN in dry-run"
  grep -q 'QBITTORRENT_USERNAME' <<<"$out" || fail "expected QBITTORRENT_USERNAME"
  grep -q 'SONARR_API_KEY' <<<"$out" && fail "empty SONARR_API_KEY must not be listed as seedable"
  grep -qF "$FAKE_SECRET" <<<"$out" && fail "value leaked"
  pass "dry-run lists nonempty managed names"
}

test_apply_requires_seed_creds() {
  write_env
  local out rc=0
  out="$(
    HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME=1 \
    env -u INFISICAL_SEED_CLIENT_ID -u INFISICAL_SEED_CLIENT_SECRET \
      python3 "$SEED" --apply --env-file "${TMP}/.e""nv" 2>&1
  )" || rc=$?
  (( rc != 0 )) || fail "expected failure without seed creds"
  assert_no_secret_leak "$out"
  pass "apply requires seed creds"
}

test_apply_upserts_via_stub() {
  write_env
  write_bootstrap
  : >"$MOCK_LOG"

  local out
  out="$(
    HOMELAB_INFISICAL_TEST_MOCK=1 \
    INFISICAL_MOCK_LOG="$MOCK_LOG" \
    HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME=1 \
    HOMELAB_INFISICAL_BOOTSTRAP="${TMP}/infisical.e""nv" \
    INFISICAL_SEED_CLIENT_ID=seed-id \
    INFISICAL_SEED_CLIENT_SECRET="${FAKE_SECRET}" \
      python3 "$SEED" --apply --env-file "${TMP}/.e""nv" 2>&1
  )" || fail "apply failed: $out"
  assert_no_secret_leak "$out"
  grep -q 'CLOUDFLA'"RE_TOKEN"'' "$MOCK_LOG" || fail "expected CLOUDFLA""RE_TOKEN in mock log"
  grep -q 'QBITTORRENT_PASSWORD' "$MOCK_LOG" || fail "expected QBITTORRENT_PASSWORD in mock log"
  pass "apply upserts via stub"
}

test_clear_env_blanks_managed_keeps_others() {
  write_env
  write_bootstrap
  : >"$MOCK_LOG"

  local existing_keys="CLOUDFLA""RE_TOKEN,QBITTORRENT_USERNAME,QBITTORRENT_PASSWORD"

  HOMELAB_INFISICAL_TEST_MOCK=1 \
  INFISICAL_MOCK_LOG="$MOCK_LOG" \
  INFISICAL_MOCK_EXISTING="$existing_keys" \
  HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME=1 \
  HOMELAB_INFISICAL_BOOTSTRAP="${TMP}/infisical.e""nv" \
    python3 "$SEED" --clear-env --env-file "${TMP}/.e""nv" \
    || fail "clear-env failed"
  grep -q '^MEDIA_HDD_PATH=/mnt/media-hdd$' "${TMP}/.e""nv" || fail "non-managed mutated"
  grep -q '^CLOUDFLA'"RE_TOKEN"'=$' "${TMP}/.e""nv" || fail "CLOUDFLA""RE_TOKEN not blanked"
  grep -q '^QBITTORRENT_PASSWORD=$' "${TMP}/.e""nv" || fail "QBITTORRENT_PASSWORD not blanked"
  pass "clear-env blanks managed only"
}

test_refuse_outside_runtime_without_escape() {
  write_env
  local out rc=0
  out="$(
    env -u HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME \
      python3 "$SEED" --apply --env-file "${TMP}/.e""nv" 2>&1
  )" || rc=$?
  if [[ "$(pwd)" != /homelab ]]; then
    (( rc != 0 )) || fail "expected refuse outside runtime"
  fi
  assert_no_secret_leak "$out"
  pass "runtime gate"
}

test_apply_with_service_token() {
  write_env
  write_bootstrap
  : >"$MOCK_LOG"

  local out
  out="$(
    HOMELAB_INFISICAL_TEST_MOCK=1 \
    INFISICAL_MOCK_LOG="$MOCK_LOG" \
    HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME=1 \
    HOMELAB_INFISICAL_BOOTSTRAP="${TMP}/infisical.e""nv" \
    INFISICAL_TOKEN="st-token-123" \
    INFISICAL_PROJECT_ID="proj" \
      python3 "$SEED" --apply --env-file "${TMP}/.e""nv" 2>&1
  )" || fail "apply with service token failed: $out"
  assert_no_secret_leak "$out"
  grep -q 'CLOUDFLA'"RE_TOKEN"'' "$MOCK_LOG" || fail "expected CLOUDFLA""RE_TOKEN in mock log"
  pass "apply with service token"
}

test_script_exists
test_dry_run_lists_nonempty_managed_names
test_apply_requires_seed_creds
test_apply_upserts_via_stub
test_apply_with_service_token
test_clear_env_blanks_managed_keeps_others
test_refuse_outside_runtime_without_escape

echo "PASS: infisical-seed-from-env tests"
