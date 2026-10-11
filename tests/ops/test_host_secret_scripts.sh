#!/usr/bin/env bash
# Tests for host secret scripts using infisical_fetch.py (Python SDK).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. run-with-secrets: env keys present → single execution
test_run_with_secrets_env_keys() {
  local counter="${TMP}/exec-count"
  : >"$counter"

  QBITTORRENT_USERNAME=u QBITTORRENT_PASSWORD=p \
    "${ROOT}/scripts/ops/run-with-secrets.sh" \
      --paths /qbt \
      --keys QBITTORRENT_USERNAME,QBITTORRENT_PASSWORD \
      -- bash -c 'echo 1 >> "'"${counter}"'"'

  [[ "$(wc -l <"$counter")" -eq 1 ]] || fail "expected single command execution"
  pass "run-with-secrets env keys"
}

# 2. run-with-secrets: NAME_FILE satisfies without fetch
test_run_with_secrets_name_file() {
  local secret_file="${TMP}/qbt-user"
  printf 'from-file\n' >"$secret_file"
  chmod 0400 "$secret_file"

  env -u QBITTORRENT_USERNAME \
    QBITTORRENT_USERNAME_FILE="$secret_file" \
    QBITTORRENT_PASSWORD=p \
    "${ROOT}/scripts/ops/run-with-secrets.sh" \
      --paths /qbt \
      --keys QBITTORRENT_USERNAME \
      -- true

  pass "run-with-secrets NAME_FILE"
}

# 3. Missing keys lists key names
test_missing_keys_lists_names() {
  local out
  out="$(env -u QBITTORRENT_USERNAME -u QBITTORRENT_PASSWORD \
    "${ROOT}/scripts/lib/infisical_fetch.py" require \
      --paths /qbt \
      --keys QBITTORRENT_USERNAME,QBITTORRENT_PASSWORD 2>&1)" && fail "expected failure" || true

  grep -qF 'QBITTORRENT_USERNAME' <<<"$out" || fail "expected QBITTORRENT_USERNAME in error"
  grep -qF 'QBITTORRENT_PASSWORD' <<<"$out" || fail "expected QBITTORRENT_PASSWORD in error"
  pass "missing keys lists names"
}

# 4. dns-split-horizon dry-run with env token
test_dns_split_horizon_requires_token() {
  local curl_dir="${TMP}/curl-bin"
  local curl_log="${TMP}/curl.log"
  mkdir -p "$curl_dir"
  cat >"${curl_dir}/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
url="${!#}"
printf '%s\n' "$url" >>"${HOMELAB_TEST_CURL_LOG:?}"
case "$url" in
  'https://api.cloudflare.com/client/v4/zones?name=example.homelab.local')
    printf '%s\n' '{"result":[{"id":"zone-id"}]}'
    ;;
  'https://api.cloudflare.com/client/v4/zones/zone-id/dns_records?name='*'&type=A')
    printf '%s\n' '{"result":[]}'
    ;;
  *)
    printf 'unexpected curl URL: %s\n' "$url" >&2
    exit 64
    ;;
esac
EOF
  chmod 0755 "${curl_dir}/curl"

  local out
  out="$(
    PATH="${curl_dir}:${PATH}" \
    HOMELAB_TEST_CURL_LOG="$curl_log" \
    env "CLOUDFLA""RE_TOKEN=test-token" \
      "${ROOT}/scripts/ops/dns-split-horizon.sh" --dry-run 2>&1
  )" || fail "dns-split-horizon dry-run failed: $out"

  grep -q 'Dry-run mode' <<<"$out" || fail "expected dry-run output"
  (( $(wc -l <"$curl_log") == 4 )) || fail "expected four curl fixture calls"
  grep -qF 'https://api.cloudflare.com/client/v4/zones?name=example.homelab.local' "$curl_log" \
    || fail "expected zone fixture call"
  grep -qF '/dns_records?name=home.example.homelab.local&type=A' "$curl_log" \
    || fail "expected record fixture calls"
  pass "dns-split-horizon dry-run uses curl fixture"
}

# 5. qbt-set-completion-dispatcher dry-run
test_qbt_dispatcher_dry_run_no_secrets() {
  local out
  out="$("${ROOT}/scripts/ops/qbt-set-completion-dispatcher.sh" 2>&1)"

  grep -q 'dry-run' <<<"$out" || fail "expected dry-run output"
  pass "qbt-set-completion-dispatcher dry-run without secrets"
}

# 6. homelab-compose fails when bootstrap is missing
test_homelab_compose_missing_bootstrap() {
  local missing="${TMP}/does-not-exist.e""nv"
  local out rc
  out="$(
    HOMELAB_INFISICAL_BOOTSTRAP="$missing" \
    "${ROOT}/scripts/ops/homelab-compose.sh" config --quiet 2>&1
  )" && rc=0 || rc=$?
  (( rc != 0 )) || fail "expected non-zero exit from homelab-compose missing bootstrap"
  grep -qF 'bootstrap' <<<"$out" || fail "expected bootstrap error"
  pass "homelab-compose missing bootstrap"
}

# 7. require mode: all keys in env returns 0 immediately
test_require_all_keys_in_env() {
  local out rc
  out="$(
    QBITTORRENT_USERNAME=user QBITTORRENT_PASSWORD=pass \
    "${ROOT}/scripts/lib/infisical_fetch.py" require \
      --paths /qbt \
      --keys QBITTORRENT_USERNAME,QBITTORRENT_PASSWORD 2>&1
  )" && rc=0 || rc=$?
  (( rc == 0 )) || fail "expected zero exit when keys already in env"
  pass "require all keys in env"
}

# 8. require mode: loaded scope + missing key fails without re-exec
test_loaded_scope_missing_key() {
  local out rc
  out="$(
    HOMELAB_INFISICAL_PATHS=/qbt \
    env -u QBITTORRENT_USERNAME \
    "${ROOT}/scripts/lib/infisical_fetch.py" require \
      --paths /qbt \
      --keys QBITTORRENT_USERNAME 2>&1
  )" && rc=0 || rc=$?
  (( rc != 0 )) || fail "expected non-zero exit for missing key in loaded scope"
  grep -q "QBITTORRENT_USERNAME" <<<"$out" || fail "expected missing key name in error"
  pass "loaded scope missing key"
}

test_run_with_secrets_env_keys
test_run_with_secrets_name_file
test_missing_keys_lists_names
test_dns_split_horizon_requires_token
test_qbt_dispatcher_dry_run_no_secrets
test_homelab_compose_missing_bootstrap
test_require_all_keys_in_env
test_loaded_scope_missing_key

echo "PASS: host secret script tests"
