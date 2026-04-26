#!/usr/bin/env bash
# Build the ephemeral CI base VM image.
# Run ONCE (no sudo needed — all files go in /home/jon/ci/).
# Subsequent CI runs create a throwaway COW overlay from this image.
#
# Requirements: qemu-system-x86_64, qemu-utils, cloud-image-utils (or genisoimage), ovmf
#
# Usage: bash scripts/build-ci-base.sh
set -euo pipefail

CI_DIR="/home/jon/ci"
BASE_IMG="${CI_DIR}/base.qcow2"
CLOUD_IMG="${CI_DIR}/noble-server-cloudimg-amd64.img"
SEED_ISO="${CI_DIR}/seed.iso"
CI_PUBKEY="${CI_DIR}/id_ci.pub"
OVMF_CODE="/usr/share/OVMF/OVMF_CODE_4M.fd"

if [[ ! -f "${CI_PUBKEY}" ]]; then
  echo "ERROR: ${CI_PUBKEY} not found. Run: ssh-keygen -t ed25519 -f ${CI_DIR}/id_ci -N ''" >&2
  exit 1
fi

mkdir -p "${CI_DIR}"

# ── Download Ubuntu 24.04 cloud image ────────────────────────────────────────
if [[ ! -f "${CLOUD_IMG}" ]]; then
  echo "Downloading Ubuntu 24.04 cloud image..."
  curl -L -o "${CLOUD_IMG}" \
    "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
fi

# ── Resize to 20 GB ───────────────────────────────────────────────────────────
echo "Creating base qcow2 from cloud image..."
cp "${CLOUD_IMG}" "${BASE_IMG}"
qemu-img resize "${BASE_IMG}" 80G

# ── Write cloud-init user-data ────────────────────────────────────────────────
CI_PUBKEY_CONTENT="$(cat "${CI_PUBKEY}")"

cat > "${CI_DIR}/user-data" <<USERDATA
#cloud-config
hostname: ci-vm
users:
  - name: ci
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - ${CI_PUBKEY_CONTENT}

package_update: true
package_upgrade: false

packages:
  - ca-certificates
  - curl
  - gnupg
  - git
  - apt-transport-https

write_files:
  - path: /etc/modules-load.d/vfio.conf
    content: |
      vfio
      vfio_iommu_type1
      vfio_pci
  - path: /etc/systemd/system/systemd-networkd-wait-online.service.d/override.conf
    content: |
      [Service]
      ExecStart=
      ExecStart=/usr/lib/systemd/systemd-networkd-wait-online --timeout=5

runcmd:
  # Install Docker (official repo)
  - install -m 0755 -d /etc/apt/keyrings
  - curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  - chmod a+r /etc/apt/keyrings/docker.asc
  - echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list
  - apt-get update -qq
  - apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  - usermod -aG docker ci
  - systemctl enable docker

  # Install NVIDIA driver + container toolkit
  - apt-get install -y linux-headers-$(uname -r) || true
  - curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  - curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  - apt-get update -qq
  - apt-get install -y nvidia-driver-550 nvidia-container-toolkit
  - nvidia-ctk runtime configure --runtime=docker
  - systemctl restart docker || true

  # Mask slow boot services for faster CI VM startup
  - systemctl mask systemd-networkd-wait-online.service

  # Disable cloud-init so it does not re-run on subsequent boots
  - touch /etc/cloud/cloud-init.disabled
  - shutdown -h now
USERDATA

cat > "${CI_DIR}/meta-data" <<METADATA
instance-id: ci-base-build
local-hostname: ci-vm
METADATA

# ── Create seed ISO (cloud-init datasource) ───────────────────────────────────
# Try native tools first, fall back to Python (pycdlib) if needed.
if command -v cloud-localds &>/dev/null; then
  cloud-localds "${SEED_ISO}" "${CI_DIR}/user-data" "${CI_DIR}/meta-data"
elif command -v genisoimage &>/dev/null; then
  genisoimage -output "${SEED_ISO}" -volid cidata -joliet -rock \
    "${CI_DIR}/user-data" "${CI_DIR}/meta-data"
elif command -v mkisofs &>/dev/null; then
  mkisofs -output "${SEED_ISO}" -volid cidata -joliet -rock \
    "${CI_DIR}/user-data" "${CI_DIR}/meta-data"
else
  echo "Generating seed ISO with Python (no cloud-image-utils found)..."
  # Requires pycdlib: pip install pycdlib
  if ! python3 -c "import pycdlib" 2>/dev/null; then
    pip3 install -q pycdlib --break-system-packages
  fi
  python3 - "${CI_DIR}/user-data" "${CI_DIR}/meta-data" "${SEED_ISO}" <<'PYEOF'
import sys
import os
import pycdlib

user_data_path, meta_data_path, output_path = sys.argv[1], sys.argv[2], sys.argv[3]

iso = pycdlib.PyCdlib()
iso.new(interchange_level=4, joliet=3, rock_ridge="1.09", vol_ident="cidata")

for src, name in [(user_data_path, "user-data"), (meta_data_path, "meta-data")]:
    with open(src, "rb") as f:
        data = f.read()
    iso.add_fp(
        fp=__import__("io").BytesIO(data),
        length=len(data),
        iso_path=f"/{name.upper().replace('-', '_')};1",
        rr_name=name,
        joliet_path=f"/{name}",
    )

iso.write(output_path)
iso.close()
print(f"Created {output_path}")
PYEOF
fi

echo "Starting provisioning VM (this will take 5-10 minutes)..."
echo "The VM will shut itself down when done."

# ── Boot VM for provisioning (no GPU, serial console) ────────────────────────
EFIVARS="${CI_DIR}/efivars-build.fd"
cp /usr/share/OVMF/OVMF_VARS_4M.fd "${EFIVARS}"

timeout 900 qemu-system-x86_64 \
  -enable-kvm \
  -cpu host \
  -smp 8 \
  -m 8G \
  -drive "file=${BASE_IMG},if=virtio,cache=writeback" \
  -drive "file=${SEED_ISO},if=virtio,media=cdrom,readonly=on" \
  -drive "if=pflash,format=raw,readonly=on,file=${OVMF_CODE}" \
  -drive "if=pflash,format=raw,file=${EFIVARS}" \
  -net user \
  -nographic \
  -serial mon:stdio \
  || true

rm -f "${EFIVARS}"

echo ""
echo "Base image provisioned: ${BASE_IMG}"
echo "Size: $(du -sh "${BASE_IMG}" | cut -f1)"
echo ""
echo "Next: add sudoers entry so the runner can call ci-vm.sh:"
echo "  echo 'jon ALL=(ALL) NOPASSWD: /home/jon/homelab/scripts/ci-vm.sh' | sudo tee /etc/sudoers.d/ci-vm"
echo "  sudo chmod 440 /etc/sudoers.d/ci-vm"
