#!/usr/bin/env bash
# Unit tests for boot orchestrator helpers (no Docker recreate).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

fail() { echo "FAIL: $1"; exit 1; }
pass() { echo "PASS: $1"; }

ORCH="${REPO_ROOT}/scripts/ops/homelab-boot-orchestrator.sh"
VERIFY="${REPO_ROOT}/scripts/ops/verify-media-mounts.sh"
UNIT="${REPO_ROOT}/config/systemd/homelab-boot-orchestrator.service"
TS_UNIT="${REPO_ROOT}/config/systemd/homelab-tailscale-binds.service"

[[ -x "${ORCH}" ]] || fail "orchestrator not executable"
[[ -x "${VERIFY}" ]] || fail "verify-media-mounts not executable"
bash -n "${ORCH}" || fail "bash -n orchestrator"
bash -n "${VERIFY}" || fail "bash -n verify-media-mounts"
bash -n "${REPO_ROOT}/scripts/ops/start-tailscale-bound-services.sh" || fail "bash -n tailscale wrapper"

# Systemd ordering and host path assertions
grep -q 'After=.*homelab-infisical-agent.service' "${UNIT}" || fail "unit missing After=homelab-infisical-agent.service"
grep -q 'Wants=.*homelab-infisical-agent.service' "${UNIT}" || fail "unit missing Wants=homelab-infisical-agent.service"
grep -q 'Conflicts=homelab-tailscale-binds.service' "${UNIT}" || fail "unit missing Conflicts="
grep -q 'StartLimitBurst=3' "${UNIT}" || fail "unit missing StartLimitBurst"
grep -q 'WorkingDirectory=/homelab' "${UNIT}" || fail "unit missing WorkingDirectory"
grep -q 'ExecStart=/homelab/scripts/ops/homelab-boot-orchestrator.sh' "${UNIT}" \
  || fail "unit ExecStart must point at runtime host path"
grep -q 'DEPRECATED' "${TS_UNIT}" || fail "legacy unit should be marked DEPRECATED"
grep -q 'homelab-boot-orchestrator.sh' "${REPO_ROOT}/scripts/ops/start-tailscale-bound-services.sh" \
  || fail "tailscale wrapper must call orchestrator"

# Rendered secret files readiness check contracts
grep -q 'check_rendered_secret_files' "${ORCH}" || fail "orchestrator missing check_rendered_secret_files"
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/lib/managed-secrets.sh"
# Source orchestrator functions safely
source "${ORCH}"
# The sourced script has its own fail() signature; restore this test's helper.
fail() { echo "FAIL: $1"; exit 1; }

SECRETS_FIXTURE_DIR="$(mktemp -d)"
# 1. Missing files
missing_out="$(check_rendered_secret_files "${SECRETS_FIXTURE_DIR}" "MISSING_KEY_1" "MISSING_KEY_2" 2>&1 || true)"
echo "${missing_out}" | grep -q 'missing.*MISSING_KEY_1' || fail "expected missing reason for MISSING_KEY_1"
echo "${missing_out}" | grep -q 'missing.*MISSING_KEY_2' || fail "expected missing reason for MISSING_KEY_2"
if check_rendered_secret_files "${SECRETS_FIXTURE_DIR}" "MISSING_KEY_1" >/dev/null 2>&1; then
  fail "check_rendered_secret_files should fail when secret file is missing"
fi

# 2. Empty file
touch "${SECRETS_FIXTURE_DIR}/EMPTY_KEY"
empty_out="$(check_rendered_secret_files "${SECRETS_FIXTURE_DIR}" "EMPTY_KEY" 2>&1 || true)"
echo "${empty_out}" | grep -q 'empty.*EMPTY_KEY' || fail "expected empty reason for EMPTY_KEY"
if check_rendered_secret_files "${SECRETS_FIXTURE_DIR}" "EMPTY_KEY" >/dev/null 2>&1; then
  fail "check_rendered_secret_files should fail when secret file is empty"
fi

# 3. Unreadable file
printf 'dummy-secret\n' > "${SECRETS_FIXTURE_DIR}/UNREADABLE_KEY"
chmod 000 "${SECRETS_FIXTURE_DIR}/UNREADABLE_KEY"
unreadable_out="$(check_rendered_secret_files "${SECRETS_FIXTURE_DIR}" "UNREADABLE_KEY" 2>&1 || true)"
echo "${unreadable_out}" | grep -q 'unreadable.*UNREADABLE_KEY' || fail "expected unreadable reason for UNREADABLE_KEY"
if check_rendered_secret_files "${SECRETS_FIXTURE_DIR}" "UNREADABLE_KEY" >/dev/null 2>&1; then
  fail "check_rendered_secret_files should fail when secret file is unreadable"
fi
chmod 600 "${SECRETS_FIXTURE_DIR}/UNREADABLE_KEY"

# Assert output never leaks dummy secret values
echo "${missing_out} ${empty_out} ${unreadable_out}" | grep -q 'dummy-secret' && fail "readiness check must never print secret values"

# 4. Complete fixture directory
rm -rf "${SECRETS_FIXTURE_DIR}"/*
while IFS= read -r key; do
  [[ -n "${key}" ]] || continue
  printf 'sentinel-%s\n' "${key}" > "${SECRETS_FIXTURE_DIR}/${key}"
done < <(managed_secret_keys)

if ! check_rendered_secret_files "${SECRETS_FIXTURE_DIR}" >/dev/null 2>&1; then
  fail "check_rendered_secret_files should pass when all managed keys exist and are non-empty"
fi
rm -rf "${SECRETS_FIXTURE_DIR}"

# Deprecation notices
grep -qi 'deprecated' "${REPO_ROOT}/scripts/ops/homelab-compose.py" || fail "homelab-compose.py missing deprecation notice"
grep -qi 'deprecated' "${REPO_ROOT}/scripts/ops/homelab-compose.sh" || fail "homelab-compose.sh missing deprecation notice"

# Phase filter + dry-run status shape (P0/P1 only; no recreate).
TMP_HOME="$(mktemp -d)"
trap 'rm -rf "${TMP_HOME}"' EXIT
# Run from repo but redirect status into temp by using a copy? Orchestrator writes under ROOT/data.
# Instead assert help and dry-run P0 against real mounts if present.
"${ORCH}" --help | grep -q -- '--dry-run' || fail "help missing --dry-run"

if [[ -d /mnt/media-hdd && -d /mnt/media-nvme ]]; then
  "${VERIFY}" >/dev/null || fail "verify-media-mounts failed on host"
  json="$("${VERIFY}" --json)"
  echo "${json}" | grep -q '"hdd_ok":true' || fail "expected hdd_ok true in JSON"
  HOMELAB_BOOT_SETTLE_SEC=0 "${ORCH}" --dry-run --phases P0,P1 >/dev/null \
    || fail "dry-run P0,P1 failed"
  status="$(cat "${REPO_ROOT}/data/boot-orchestrator/status.json")"
  echo "${status}" | grep -q '"phase": "P1"' || fail "status phase not P1"
  echo "${status}" | grep -q '"dry_run": true' || fail "status dry_run not true"
  echo "${status}" | grep -q '"warnings"' || fail "status missing warnings array"
  pass "dry-run P0,P1 + status.json shape"
else
  pass "skip mount live checks (paths absent)"
fi

# Compose contract: worker start_period bumped
grep -q 'homelab-qbt-offload' compose/workers.yml || fail "offload service missing"
awk '/homelab-qbt-offload:/,/^  [a-z]/{print}' compose/workers.yml | grep -q 'start_period: 120s' \
  || fail "offload start_period should be 120s"

# Docs catalog mentions last_run_failed as soft warning
grep -q 'last_run_failed' docs/boot-orchestrator.md || fail "docs missing last_run_failed"
grep -q 'warnings' docs/boot-orchestrator.md || fail "docs should mention warnings[]"
grep -q 'ingress_route_not_ready' docs/boot-orchestrator.md || fail "docs missing ingress_route_not_ready"
grep -q 'boot:' config/workers.yaml.example || fail "workers YAML missing boot section"
grep -q 'ingress_smoke:' config/workers.yaml.example || fail "workers YAML missing ingress_smoke section"

# P2 must list filebrowser (media bind) and must not list media-agent (API-only).
grep -q 'filebrowser' scripts/ops/homelab-boot-orchestrator.sh || fail "P2 list missing filebrowser"
if grep -A20 'DEFAULT_MOUNT_BIND_SERVICES' scripts/ops/homelab-boot-orchestrator.sh | grep -q 'media-agent'; then
  fail "P2 list should not include media-agent"
fi
# Root force-recreate uses secret-aware wrapper (compatibility).
# shellcheck disable=SC2016
grep -q 'wrapper="${ROOT}/scripts/ops/homelab-compose.py"' scripts/ops/homelab-boot-orchestrator.sh \
  || fail "compose_recreate must define homelab-compose.py wrapper"
# shellcheck disable=SC2016
grep -q '"${wrapper}" config --services' scripts/ops/homelab-boot-orchestrator.sh \
  || fail "compose_recreate membership check must use secret-aware wrapper"
# Infisical/Compose failure must be fatal — never masked as "missing service".
if grep -A6 'compose_services=.*config --services' scripts/ops/homelab-boot-orchestrator.sh \
  | grep -q '2>/dev/null'; then
  fail "compose_recreate must not hide wrapper config errors with 2>/dev/null"
fi
grep -q 'secret-aware Compose config failed during membership check' \
  scripts/ops/homelab-boot-orchestrator.sh \
  || fail "compose_recreate must hard-fail when wrapper config fails"
# shellcheck disable=SC2016
grep -q '"${wrapper}" up -d --no-deps --force-recreate' scripts/ops/homelab-boot-orchestrator.sh \
  || fail "compose_recreate must use homelab-compose.py for force-recreate"
grep -q 'run_ingress_smoke' scripts/ops/homelab-boot-orchestrator.sh \
  || fail "orchestrator missing ingress smoke function"
grep -q 'ingress_route_not_ready' scripts/ops/homelab-boot-orchestrator.sh \
  || fail "orchestrator missing ingress_route_not_ready reason"
# mark_phase_done P2 must appear after stale_media_bind fail path in the file order
p2_mark="$(awk '/phase_p2_mount_binds/,/^phase_p3_/{ if (/mark_phase_done P2/) print NR }' scripts/ops/homelab-boot-orchestrator.sh | head -1)"
p2_stale="$(awk '/phase_p2_mount_binds/,/^phase_p3_/{ if (/stale_media_bind/) print NR }' scripts/ops/homelab-boot-orchestrator.sh | head -1)"
[[ -n "${p2_mark}" && -n "${p2_stale}" ]] || fail "could not locate P2 mark/stale lines"
(( p2_mark > p2_stale )) || fail "mark_phase_done P2 must come after stale_media_bind check"

pass "boot orchestrator contracts"
