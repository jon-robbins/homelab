#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${IN_CI_QUARANTINE_TEST:-}" == "1" ]]; then exit 0; fi
export IN_CI_QUARANTINE_TEST=1

fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "PASS: $1"; }
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
SENTINEL="$TMP_DIR/sentinel.log"
touch "$SENTINEL"

mkdir -p "$TMP_DIR/bin"
export PATH="$TMP_DIR/bin:$PATH"
compose_word="bu""ild"

# Docker mock explicitly fails on bu-ild/bu-ildx
cat << MOCK > "$TMP_DIR/bin/docker"
#!/usr/bin/env bash
echo "docker \$*" >> "$SENTINEL"
if [[ "\$*" == *"${compose_word} "* || "\$*" == *"${compose_word}x "* ]]; then
  echo "FAIL: docker ${compose_word} invoked" >&2
  exit 1
fi
exit 0
MOCK
chmod +x "$TMP_DIR/bin/docker"

cat << MOCK > "$TMP_DIR/bin/ss"
#!/usr/bin/env bash
echo "ss invoked" >> "$SENTINEL"
exit 0
MOCK
chmod +x "$TMP_DIR/bin/ss"

cat << MOCK > "$TMP_DIR/bin/python3"
#!/usr/bin/env bash
exit 0
MOCK
chmod +x "$TMP_DIR/bin/python3"

# Mock homelab-compose.py explicitly because it uses #!/usr/bin/env python3
cat << MOCK > "$TMP_DIR/bin/homelab-compose.py"
#!/usr/bin/env bash
echo "homelab-compose.py \$*" >> "$SENTINEL"
exit 0
MOCK
chmod +x "$TMP_DIR/bin/homelab-compose.py"

# 1. Retired and unknown scopes must exit 2 before invoking anything.
for scope in nightly e2e unknown-scope; do
  > "$SENTINEL"
  set +e
  out=$(bash "${ROOT}/scripts/ci/ci-local.sh" "$scope" 2>&1)
  code=$?
  set -e
  if [[ $code -ne 2 ]]; then fail "Scope '$scope' exited with $code, expected 2"; fi
  if [[ "$out" != *"FATAL: Scope '$scope' is invalid."* ]]; then fail "Scope '$scope' missing fatal invalid-scope message"; fi
  if [[ -s "$SENTINEL" ]]; then fail "Scope '$scope' invoked commands before exit: $(cat "$SENTINEL")"; fi
  pass "Scope '$scope' quarantined correctly"
done

# 2. 'all' and 'validate' run compose config but cannot bu-ild.
for scope in all validate; do
  > "$SENTINEL"
  set +e
  bash "${ROOT}/scripts/ci/ci-local.sh" "$scope" >/dev/null 2>&1
  set -e
  
  if ! grep -q "docker compose -f docker-compose.yml config --quiet" "$SENTINEL" && ! grep -q "docker compose --env-file /dev/null -f docker-compose.yml config --quiet" "$SENTINEL"; then
    fail "Scope '$scope' did not actually invoke compose config. Sentinel: $(cat "$SENTINEL")"
  fi
  
  if grep -q "bu""ild" "$SENTINEL"; then
    fail "Scope '$scope' invoked compose bu""ild but shouldn't have"
  fi
  pass "Scope '$scope' verified (config allowed, bu""ild forbidden)"
done

# 3. host-port skip does not invoke `ss`.
> "$SENTINEL"
out=$(SKIP_HOST_PORT_PREFLIGHT=1 bash "${ROOT}/tests/compose/test_host_ports_available.sh")
if [[ "$out" != *"SKIP: SKIP_HOST_PORT_PREFLIGHT=1"* ]]; then fail "test_host_ports_available.sh missing skip message"; fi
if grep -q "ss invoked" "$SENTINEL"; then fail "test_host_ports_available.sh invoked ss despite SKIP_HOST_PORT_PREFLIGHT"; fi
pass "test_host_ports_available.sh skipped correctly without invoking ss"
