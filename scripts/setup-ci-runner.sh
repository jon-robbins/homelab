#!/usr/bin/env bash
# One-time setup for the self-hosted CI runner.
# Run with sudo: sudo bash scripts/setup-ci-runner.sh
#
# Does:
#   1. Adds the runner user to the kvm group (for QEMU/KVM access)
#   2. Adds a targeted sudoers entry so ci-vm.sh can be called without password
set -euo pipefail

RUNNER_USER="${1:-jon}"
SCRIPT_PATH="/home/jon/homelab/scripts/ci-vm.sh"

echo "Setting up CI runner for user: ${RUNNER_USER}"

# ── KVM group ─────────────────────────────────────────────────────────────────
if id -nG "${RUNNER_USER}" | grep -qw kvm; then
  echo "  [OK] ${RUNNER_USER} already in kvm group"
else
  usermod -aG kvm "${RUNNER_USER}"
  echo "  [DONE] Added ${RUNNER_USER} to kvm group (takes effect on next login/newgrp)"
fi

# ── Sudoers entry ─────────────────────────────────────────────────────────────
SUDOERS_FILE="/etc/sudoers.d/ci-vm"
SUDOERS_LINE="${RUNNER_USER} ALL=(ALL) NOPASSWD: ${SCRIPT_PATH}"

if [[ -f "${SUDOERS_FILE}" ]] && grep -qF "${SUDOERS_LINE}" "${SUDOERS_FILE}"; then
  echo "  [OK] Sudoers entry already present"
else
  echo "${SUDOERS_LINE}" > "${SUDOERS_FILE}"
  chmod 440 "${SUDOERS_FILE}"
  echo "  [DONE] Created ${SUDOERS_FILE}"
fi

echo ""
echo "Setup complete. If jon was just added to the kvm group, run:"
echo "  newgrp kvm"
echo "or log out and back in, then re-run the CI base image build:"
echo "  bash scripts/build-ci-base.sh"
