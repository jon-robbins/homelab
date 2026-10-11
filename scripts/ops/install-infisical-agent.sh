#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

BOOTSTRAP_PATH="${HOMELAB_INFISICAL_BOOTSTRAP:-${HOME}/.config/homelab/infisical.env}"
CONFIG_SOURCE_DEFAULT="${REPO_ROOT}/config/infisical/agent-config.yaml.example"
SECRET_DIR_DEFAULT="${HOME}/.config/homelab/secrets"
ETC_DIR="${HOMELAB_INFISICAL_AGENT_ETC_DIR:-/etc/infisical-agent}"
SYSTEMD_DIR="${HOMELAB_INFISICAL_AGENT_SYSTEMD_DIR:-/etc/systemd/system}"
INFISICAL_BIN_CHECK="${HOMELAB_INFISICAL_AGENT_BIN:-/usr/bin/infisical}"
AGENT_USER="${HOMELAB_INFISICAL_AGENT_USER:-${SUDO_USER:-${USER:?USER must be set}}}"
AGENT_GROUP="${HOMELAB_INFISICAL_AGENT_GROUP:-${AGENT_USER}}"

CONFIG_SOURCE="$CONFIG_SOURCE_DEFAULT"
SECRET_DIR="$SECRET_DIR_DEFAULT"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [--config-source PATH] [--secret-dir PATH]

Installs the stock Infisical Agent host config and systemd unit without
starting the service. Reads bootstrap credentials from:
  ${BOOTSTRAP_PATH}
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config-source)
      [[ $# -ge 2 ]] || fail "--config-source requires a path"
      CONFIG_SOURCE="$2"
      shift 2
      ;;
    --secret-dir)
      [[ $# -ge 2 ]] || fail "--secret-dir requires a path"
      SECRET_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -x "$INFISICAL_BIN_CHECK" ]] || fail "infisical binary not found or not executable: $INFISICAL_BIN_CHECK"
[[ -f "$CONFIG_SOURCE" ]] || fail "Agent config source not found: $CONFIG_SOURCE"

validate_bootstrap() {
  [[ -f "$BOOTSTRAP_PATH" ]] || fail "bootstrap file not found: $BOOTSTRAP_PATH"

  local mode owner_uid expected_uid
  mode="$(stat -c '%a' "$BOOTSTRAP_PATH")"
  owner_uid="$(stat -c '%u' "$BOOTSTRAP_PATH")"
  if (( 8#${mode} & 077 )); then
    fail "bootstrap file must not be group/other accessible: $BOOTSTRAP_PATH"
  fi

  if [[ $EUID -eq 0 ]]; then
    expected_uid="$(id -u "$AGENT_USER")"
  else
    expected_uid="$(id -u)"
  fi
  [[ "$owner_uid" == "$expected_uid" ]] || fail "bootstrap file owner uid (${owner_uid}) must match ${AGENT_USER} (${expected_uid}): $BOOTSTRAP_PATH"
}

parse_bootstrap() {
  local line key value
  INFISICAL_CLIENT_ID=""
  INFISICAL_CLIENT_SECRET=""
  INFISICAL_PROJECT_ID=""
  INFISICAL_ENV="prod"
  INFISICAL_API_URL="https://app.infisical.com"

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -n "$line" ]] || continue
    [[ "${line:0:1}" == "#" ]] && continue
    [[ "$line" == INFISICAL_*=* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      INFISICAL_CLIENT_ID) INFISICAL_CLIENT_ID="$value" ;;
      INFISICAL_CLIENT_SECRET) INFISICAL_CLIENT_SECRET="$value" ;;
      INFISICAL_PROJECT_ID) INFISICAL_PROJECT_ID="$value" ;;
      INFISICAL_ENV) INFISICAL_ENV="$value" ;;
      INFISICAL_API_URL) INFISICAL_API_URL="$value" ;;
    esac
  done <"$BOOTSTRAP_PATH"

  [[ -n "$INFISICAL_CLIENT_ID" ]] || fail "bootstrap missing required field: INFISICAL_CLIENT_ID"
  [[ -n "$INFISICAL_CLIENT_SECRET" ]] || fail "bootstrap missing required field: INFISICAL_CLIENT_SECRET"
  [[ -n "$INFISICAL_PROJECT_ID" ]] || fail "bootstrap missing required field: INFISICAL_PROJECT_ID"
  [[ -n "$INFISICAL_ENV" ]] || fail "bootstrap missing required field: INFISICAL_ENV"
  [[ -n "$INFISICAL_API_URL" ]] || fail "bootstrap missing required field: INFISICAL_API_URL"
}

ensure_parent_dirs() {
  install -d -m 0700 "$SECRET_DIR"
  install -d -m 0755 "$ETC_DIR"
  install -d -m 0700 "${ETC_DIR}/auth"
  install -d -m 0755 "$SYSTEMD_DIR"
}

maybe_chown() {
  local path="$1"
  if [[ $EUID -eq 0 ]]; then
    chown "${AGENT_USER}:${AGENT_GROUP}" "$path"
  fi
}

write_credential_files() {
  local client_id_path="${ETC_DIR}/auth/client-id"
  local client_secret_path="${ETC_DIR}/auth/client-secret"

  umask 077
  printf '%s\n' "$INFISICAL_CLIENT_ID" >"$client_id_path"
  printf '%s\n' "$INFISICAL_CLIENT_SECRET" >"$client_secret_path"
  chmod 0600 "$client_id_path" "$client_secret_path"
  maybe_chown "$client_id_path"
  maybe_chown "$client_secret_path"
}

write_config() {
  local config_path="${ETC_DIR}/config.yaml"
  local tmp_config
  tmp_config="$(mktemp)"

  {
    echo "# Installed by scripts/ops/install-infisical-agent.sh"
    echo "infisical:"
    printf '  address: "%s"\n' "$INFISICAL_API_URL"
    echo "auth:"
    echo '  type: "universal-auth"'
    echo "  config:"
    printf '    client-id: "%s"\n' "${ETC_DIR}/auth/client-id"
    printf '    client-secret: "%s"\n' "${ETC_DIR}/auth/client-secret"
    echo "    remove_client_secret_on_read: false"
    sed "s|destination-path: \${HOME}/.config/homelab/secrets/|destination-path: ${SECRET_DIR%/}/|g" "$CONFIG_SOURCE"
  } >"$tmp_config"

  install -m 0600 "$tmp_config" "$config_path"
  rm -f "$tmp_config"
  maybe_chown "$config_path"
}

install_unit() {
  local unit_source="${REPO_ROOT}/config/systemd/homelab-infisical-agent.service"
  local unit_target="${SYSTEMD_DIR}/homelab-infisical-agent.service"
  local tmp_unit
  [[ -f "$unit_source" ]] || fail "unit source not found: $unit_source"
  tmp_unit="$(mktemp)"
  sed -e "s/^User=.*/User=${AGENT_USER}/" -e "s/^Group=.*/Group=${AGENT_GROUP}/" \
    "$unit_source" >"$tmp_unit"
  install -m 0644 "$tmp_unit" "$unit_target"
  rm -f "$tmp_unit"
}

print_next_steps() {
  echo "Installed secret directory: ${SECRET_DIR}"
  echo "Installed Agent credentials: ${ETC_DIR}/auth/client-id and ${ETC_DIR}/auth/client-secret"
  echo "Installed Agent config: ${ETC_DIR}/config.yaml"
  echo "Installed systemd unit: ${SYSTEMD_DIR}/homelab-infisical-agent.service"
  echo "Next steps on the runtime host:"
  echo "  sudo systemctl daemon-reload"
  echo "  sudo systemctl enable --now homelab-infisical-agent.service"
  echo "  sudo systemctl status homelab-infisical-agent.service"
}

main() {
  validate_bootstrap
  parse_bootstrap
  ensure_parent_dirs
  maybe_chown "$SECRET_DIR"
  maybe_chown "$ETC_DIR"
  maybe_chown "${ETC_DIR}/auth"
  write_credential_files
  write_config
  install_unit
  print_next_steps
}

main "$@"
