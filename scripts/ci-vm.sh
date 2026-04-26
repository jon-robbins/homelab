#!/usr/bin/env bash
# Ephemeral CI VM manager.
#
# Requires sudo (VFIO bind/unbind and QEMU with vfio-pci device).
# Add sudoers entry once:
#   echo 'jon ALL=(ALL) NOPASSWD: /home/jon/homelab/scripts/ci-vm.sh' | sudo tee /etc/sudoers.d/ci-vm
#   sudo chmod 440 /etc/sudoers.d/ci-vm
#
# Usage (called from GitHub Actions):
#   sudo scripts/ci-vm.sh boot   <run_id> <repo_url> <git_ref>
#   sudo scripts/ci-vm.sh test   <run_id>
#   sudo scripts/ci-vm.sh teardown <run_id>
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
CI_DIR="/home/jon/ci"
BASE_IMG="${CI_DIR}/base.qcow2"
SSH_KEY="${CI_DIR}/id_ci"
OVMF_CODE="/usr/share/OVMF/OVMF_CODE_4M.fd"

VM_CPUS=8
VM_MEM=16G
VM_SSH_PORT=2222

GPU_GFX="0000:01:00.0"
GPU_AUD="0000:01:00.1"
GPU_GFX_ID="10de 2204"
GPU_AUD_ID="10de 1aef"

# ── Helpers ───────────────────────────────────────────────────────────────────
log() { echo "[ci-vm] $*"; }
fail() { echo "[ci-vm] FAIL: $*" >&2; exit 1; }

vm_disk() { echo "/tmp/ci-vm-${1}.qcow2"; }
vm_pid_file() { echo "/tmp/ci-vm-${1}.pid"; }
vm_efivars() { echo "/tmp/ci-vm-${1}-efivars.fd"; }

ssh_vm() {
  local run_id="$1"; shift
  ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=5 \
    -p "${VM_SSH_PORT}" \
    ci@localhost "$@"
}

wait_for_ssh() {
  local run_id="$1"
  local deadline=$(( SECONDS + 180 ))
  log "Waiting for VM SSH (up to 180s)..."
  while (( SECONDS < deadline )); do
    if ssh_vm "${run_id}" true 2>/dev/null; then
      log "VM is up"
      return 0
    fi
    sleep 5
  done
  fail "VM did not respond to SSH within 180s"
}

# ── GPU VFIO bind/unbind ──────────────────────────────────────────────────────
gpu_to_vfio() {
  log "Binding GPU to vfio-pci..."
  modprobe vfio-pci 2>/dev/null || true

  # Unbind from current drivers
  for dev in "${GPU_GFX}" "${GPU_AUD}"; do
    local driver_link="/sys/bus/pci/devices/${dev}/driver"
    if [[ -L "${driver_link}" ]]; then
      local current_driver
      current_driver="$(basename "$(readlink -f "${driver_link}")")"
      log "  Unbinding ${dev} from ${current_driver}"
      echo "${dev}" > "/sys/bus/pci/drivers/${current_driver}/unbind" || true
    fi
  done

  sleep 1

  # Bind to vfio-pci
  for id in ${GPU_GFX_ID} ${GPU_AUD_ID}; do
    echo "${id}" > /sys/bus/pci/drivers/vfio-pci/new_id 2>/dev/null || true
  done

  for dev in "${GPU_GFX}" "${GPU_AUD}"; do
    if [[ ! -L "/sys/bus/pci/devices/${dev}/driver" ]]; then
      echo "vfio-pci" > "/sys/bus/pci/devices/${dev}/driver_override"
      echo "${dev}" > /sys/bus/pci/drivers_probe
    fi
  done

  log "GPU bound to vfio-pci"
}

gpu_to_nvidia() {
  log "Rebinding GPU to nvidia..."
  for dev in "${GPU_GFX}" "${GPU_AUD}"; do
    local driver_link="/sys/bus/pci/devices/${dev}/driver"
    if [[ -L "${driver_link}" ]]; then
      local current_driver
      current_driver="$(basename "$(readlink -f "${driver_link}")")"
      if [[ "${current_driver}" == "vfio-pci" ]]; then
        echo "${dev}" > "/sys/bus/pci/drivers/vfio-pci/unbind" || true
      fi
    fi
    echo "" > "/sys/bus/pci/devices/${dev}/driver_override" 2>/dev/null || true
  done

  # Rescan so nvidia reclaims the GPU
  echo 1 > /sys/bus/pci/rescan || true
  sleep 2

  # Trigger nvidia driver probe
  echo "${GPU_GFX}" > /sys/bus/pci/drivers/nvidia/bind 2>/dev/null || true
  modprobe nvidia 2>/dev/null || true

  log "GPU rebound to nvidia"
}

# ── Boot ──────────────────────────────────────────────────────────────────────
cmd_boot() {
  local run_id="$1"
  local disk efivars

  disk="$(vm_disk "${run_id}")"
  efivars="$(vm_efivars "${run_id}")"
  pid_file="$(vm_pid_file "${run_id}")"

  [[ -f "${BASE_IMG}" ]] || fail "Base image not found: ${BASE_IMG}. Run scripts/build-ci-base.sh first."

  log "Creating COW overlay disk for run ${run_id}..."
  qemu-img create -q -b "${BASE_IMG}" -F qcow2 -f qcow2 "${disk}"
  cp /usr/share/OVMF/OVMF_VARS_4M.fd "${efivars}"

  gpu_to_vfio

  log "Booting VM (${VM_CPUS} CPUs, ${VM_MEM} RAM)..."
  qemu-system-x86_64 \
    -enable-kvm \
    -cpu host \
    -smp "${VM_CPUS}" \
    -m "${VM_MEM}" \
    -drive "file=${disk},if=virtio,cache=writeback" \
    -drive "if=pflash,format=raw,readonly=on,file=${OVMF_CODE}" \
    -drive "if=pflash,format=raw,file=${efivars}" \
    -device "vfio-pci,host=${GPU_GFX},multifunction=on" \
    -device "vfio-pci,host=${GPU_AUD}" \
    -netdev "user,id=net0,hostfwd=tcp::${VM_SSH_PORT}-:22" \
    -device "virtio-net-pci,netdev=net0" \
    -display none \
    -daemonize \
    -pidfile "${pid_file}"

  log "VM started (pid $(cat "${pid_file}"))"
  wait_for_ssh "${run_id}"

  # Grow the filesystem to fill the virtual disk size.
  # The COW overlay inherits the virtual size of the base image, but the
  # filesystem was only sized to fill the disk at provisioning time.
  log "Growing filesystem in VM..."
  ssh_vm "${run_id}" bash -c "
    sudo growpart /dev/vda 1 2>/dev/null || true
    sudo resize2fs /dev/vda1 2>/dev/null || true
    df -h / | tail -1
  "
}

# ── Test ──────────────────────────────────────────────────────────────────────
cmd_test() {
  local run_id="$1"
  # workspace_dir is the host path to the checked-out repo (avoids GitHub auth).
  # Pass it explicitly: ci-vm.sh test <run_id> <workspace_dir>
  local workspace_dir="$2"

  log "Running tests in VM for run ${run_id}..."

  # Copy the repo from the CI workspace into the VM via scp.
  # This avoids needing GitHub credentials for private repos.
  log "Copying repo from ${workspace_dir} to VM..."
  scp -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -P "${VM_SSH_PORT}" \
    -r "${workspace_dir}" \
    ci@localhost:/home/ci/homelab

  ssh_vm "${run_id}" bash -s <<'REMOTE'
set -euo pipefail
REPO_DIR="/home/ci/homelab"

echo "[vm] Repo copied to ${REPO_DIR}"

cd "${REPO_DIR}"

# Source .env if present (CI should pass secrets via env or .env in VM)
if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

# Create required external networks before compose up
docker network create homelab_net 2>/dev/null || true

# Pre-create bind-mount files/dirs that compose expects to exist.
# Docker auto-creates missing bind-mount paths as directories, which causes
# file-vs-directory conflicts and missing-content healthcheck failures.
mkdir -p data/dashy
if [[ ! -f data/dashy/conf.yml ]]; then
  if [[ -f config/dashy/conf.yml.example ]]; then
    cp config/dashy/conf.yml.example data/dashy/conf.yml
  else
    echo "pageInfo: {title: CI}" > data/dashy/conf.yml
  fi
fi

# internal-dashboard: nginx serves this dir; empty dir → 403 → healthcheck fails.
mkdir -p data/internal-dashboard
[[ -f data/internal-dashboard/index.html ]] || echo "<html><body>CI</body></html>" > data/internal-dashboard/index.html

# *arr apps: healthchecks use the URL base path (e.g. /sonarr/ping).
# On first start with an empty config dir the app hasn't written config.xml yet,
# so the base path isn't set and the healthcheck returns 404.
# Pre-seeding a minimal config.xml with UrlBase lets the app start healthy.
for app_cfg in \
  "data/sonarr/config.xml|/sonarr" \
  "data/radarr/config.xml|/radarr" \
  "data/readarr/config.xml|/readarr"; do
  cfg_path="${app_cfg%%|*}"
  url_base="${app_cfg##*|}"
  mkdir -p "$(dirname "$cfg_path")"
  if [[ ! -f "$cfg_path" ]]; then
    cat > "$cfg_path" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<Config>
  <UrlBase>${url_base}</UrlBase>
</Config>
EOF
  fi
done

# Jackett uses JSON config; base path must match /jackett healthcheck path.
mkdir -p data/jackett/Jackett
if [[ ! -f data/jackett/Jackett/ServerConfig.json ]]; then
  echo '{"BasePathOverride":"/jackett"}' > data/jackett/Jackett/ServerConfig.json
fi

# Build locally-tagged images that aren't in any public registry
echo "[vm] Building local images..."
if [[ -f caddy/Dockerfile ]]; then
  docker build -t local/caddy-cf:latest caddy/
fi
if [[ -f src/homelab_workers/Dockerfile ]]; then
  docker build -t local/homelab-workers:latest src/homelab_workers/
fi

echo "[vm] Starting stack..."
docker compose \
  -f docker-compose.network.yml \
  -f docker-compose.media.yml \
  -f docker-compose.llm.yml \
  up -d --build

echo "[vm] Waiting for healthchecks (up to 300s)..."
  # Services that require external credentials and are expected unhealthy in CI
  # without those credentials being injected via .env or secrets.
  # These are warned but do not fail the job; add real secrets to enable them.
  CREDENTIAL_SERVICES="gluetun cloudflared tailscale pihole qbittorrent torrent-health-ui openclaw"

deadline=$(( SECONDS + 300 ))
while (( SECONDS < deadline )); do
  all_ready=true
  for c in $(docker ps --format '{{.Names}}'); do
    status=$(docker inspect --format='{{.State.Health.Status}}' "$c" 2>/dev/null || echo "none")
    # Skip credential-dependent services in the wait loop
    is_cred=false
    for svc in ${CREDENTIAL_SERVICES}; do
      [[ "$c" == *"$svc"* ]] && is_cred=true && break
    done
    $is_cred && continue
    case "$status" in
      healthy|none) ;;
      *) all_ready=false ;;
    esac
  done
  $all_ready && break
  echo "[vm] Waiting... ($((deadline - SECONDS))s left)"
  sleep 5
done

# Final healthcheck report
failed=0
for c in $(docker ps --format '{{.Names}}'); do
  status=$(docker inspect --format='{{.State.Health.Status}}' "$c" 2>/dev/null || echo "none")
  # Credential-dependent services: warn but don't fail
  is_cred=false
  for svc in ${CREDENTIAL_SERVICES}; do
    [[ "$c" == *"$svc"* ]] && is_cred=true && break
  done
  if [[ "$status" == "unhealthy" || "$status" == "starting" ]]; then
    if $is_cred; then
      echo "[vm] WARN (no credentials in CI): $c ($status)"
    else
      echo "[vm] FAIL: $c ($status)"
      docker inspect --format='{{json .State.Health}}' "$c"
      failed=$(( failed + 1 ))
    fi
  else
    echo "[vm] OK: $c ($status)"
  fi
done
(( failed == 0 ))

echo "[vm] Running integration tests..."
bash tests/integration/router-smoke-p0.sh
bash tests/integration/media-pipeline-e2e.sh
bash tests/integration/llm-pipeline-e2e.sh
python3 tests/integration/llm_behavior_check.py

if [[ "${CI_LOCAL_RUNTIME_SMOKE:-0}" == "1" ]]; then
  bash tests/runtime/run_tests.sh
fi

echo "[vm] All tests passed."
REMOTE
}

# ── Teardown ──────────────────────────────────────────────────────────────────
cmd_teardown() {
  local run_id="$1"
  local disk pid_file efivars

  disk="$(vm_disk "${run_id}")"
  pid_file="$(vm_pid_file "${run_id}")"
  efivars="$(vm_efivars "${run_id}")"

  log "Tearing down VM for run ${run_id}..."

  if [[ -f "${pid_file}" ]]; then
    local pid
    pid="$(cat "${pid_file}")"
    kill "${pid}" 2>/dev/null || true
    sleep 2
    kill -9 "${pid}" 2>/dev/null || true
    rm -f "${pid_file}"
    log "VM process ${pid} terminated"
  fi

  gpu_to_nvidia

  rm -f "${disk}" "${efivars}"
  log "Teardown complete"
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
CMD="${1:-}"
case "${CMD}" in
  boot)
    [[ $# -ge 2 ]] || fail "Usage: ci-vm.sh boot <run_id>"
    cmd_boot "$2"
    ;;
  test)
    [[ $# -ge 3 ]] || fail "Usage: ci-vm.sh test <run_id> <workspace_dir>"
    cmd_test "$2" "$3"
    ;;
  teardown)
    [[ $# -ge 2 ]] || fail "Usage: ci-vm.sh teardown <run_id>"
    cmd_teardown "$2"
    ;;
  *)
    echo "Usage: $0 {boot|test|teardown} <run_id> [args...]" >&2
    exit 1
    ;;
esac
