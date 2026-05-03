#!/usr/bin/env bash
set -euo pipefail

# Normalize media ownership and permissions for arr stack imports.
# Intended to be run manually with sudo.

DRY_RUN=0
OWNER_NAME="${SUDO_USER:-jon}"
GROUP_NAME="plex"
TARGET_PATHS=(
  "/mnt/media-hdd/TV"
  "/mnt/media-hdd/Movies"
  "/mnt/media-nvme/Incoming"
)

usage() {
  cat <<'EOF'
Usage:
  sudo ./scripts/ops/fix-media-permissions.sh [options]

Options:
  --dry-run            Print actions without applying changes
  --owner <user>       Owner user (default: current sudo user, fallback jon)
  --group <group>      Group owner (default: plex)
  --path <path>        Add/override target path (can be provided multiple times)
  -h, --help           Show this help

Examples:
  sudo ./scripts/ops/fix-media-permissions.sh --dry-run
  sudo ./scripts/ops/fix-media-permissions.sh --owner jon --group plex
  sudo ./scripts/ops/fix-media-permissions.sh --path /mnt/media-hdd/TV --path /mnt/media-nvme/Incoming
EOF
}

run_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    printf '[run] %s\n' "$*"
    eval "$*"
  fi
}

PATHS_FROM_CLI=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --owner)
      OWNER_NAME="${2:-}"
      shift 2
      ;;
    --group)
      GROUP_NAME="${2:-}"
      shift 2
      ;;
    --path)
      PATHS_FROM_CLI+=("${2:-}")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "${#PATHS_FROM_CLI[@]}" -gt 0 ]]; then
  TARGET_PATHS=("${PATHS_FROM_CLI[@]}")
fi

if [[ "${EUID}" -ne 0 ]]; then
  printf 'Run as root (use sudo).\n' >&2
  exit 1
fi

if ! id "$OWNER_NAME" >/dev/null 2>&1; then
  printf 'Owner user does not exist: %s\n' "$OWNER_NAME" >&2
  exit 1
fi

if ! getent group "$GROUP_NAME" >/dev/null 2>&1; then
  printf 'Group does not exist: %s\n' "$GROUP_NAME" >&2
  exit 1
fi

printf 'Owner: %s\n' "$OWNER_NAME"
printf 'Group: %s\n' "$GROUP_NAME"
printf 'Dry run: %s\n' "$DRY_RUN"
printf 'Targets:\n'
for p in "${TARGET_PATHS[@]}"; do
  printf '  - %s\n' "$p"
done

for path in "${TARGET_PATHS[@]}"; do
  if [[ -z "$path" ]]; then
    continue
  fi

  if [[ ! -d "$path" ]]; then
    printf '[skip] Missing directory: %s\n' "$path"
    continue
  fi

  printf '\n== Processing: %s ==\n' "$path"
  run_cmd "chown -R \"$OWNER_NAME:$GROUP_NAME\" \"$path\""
  run_cmd "find \"$path\" -type d -exec chmod 2775 {} +"
  run_cmd "find \"$path\" -type f -exec chmod 664 {} +"
done

printf '\nDone.\n'
