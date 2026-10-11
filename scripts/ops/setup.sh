#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

confirm() {
    local prompt="${1:-Continue?}"
    read -r -p "$prompt [y/N] " response
    [[ "$response" =~ ^[Yy]$ ]]
}

get_env_value() {
    local key="$1"
    local file="${2:-.env}"
    if [[ ! -f "$file" ]]; then
        return 0
    fi
    awk -F= -v target="$key" '$1 == target {print substr($0, index($0, "=") + 1); exit}' "$file"
}

escape_sed_replacement() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//&/\\&}"
    value="${value//|/\\|}"
    printf '%s' "$value"
}

set_env_value() {
    local key="$1"
    local value="$2"
    local escaped
    escaped="$(escape_sed_replacement "$value")"
    if grep -qE "^${key}=" .env; then
        sed -i "s|^${key}=.*|${key}=${escaped}|" .env
    else
        printf '%s=%s\n' "$key" "$value" >> .env
    fi
}

prompt_env_value() {
    local key="$1"
    local prompt="$2"
    local current default input chosen

    current="$(get_env_value "$key" ".env")"
    default="$(get_env_value "$key" ".env.example")"
    if [[ -z "$current" ]]; then
        current="$default"
    fi

    if [[ -t 0 ]]; then
        read -r -p "${prompt} [${current}]: " input
        chosen="${input:-$current}"
    else
        chosen="$current"
        warn "Non-interactive shell: keeping ${key}=${chosen}"
    fi
    set_env_value "$key" "$chosen"
    info "Set ${key}=${chosen}"
}

ensure_env_file() {
    if [[ -f .env ]]; then
        info ".env already exists; preserving existing file"
        return
    fi
    if [[ ! -f .env.example ]]; then
        error ".env.example not found; cannot initialize .env"
        exit 1
    fi
    cp .env.example .env
    info "Created .env from .env.example"
}

setup_env() {
    ensure_env_file
    prompt_env_value "MEDIA_HDD_PATH" "Enter MEDIA_HDD_PATH"
    prompt_env_value "MEDIA_NVME_PATH" "Enter MEDIA_NVME_PATH"
    prompt_env_value "PLEX_DATA_PATH" "Enter PLEX_DATA_PATH"
}

copy_template_if_missing() {
    local src="$1"
    local dest="$2"
    if [[ -f "$dest" ]]; then
        info "Config exists, keeping ${dest}"
        return
    fi
    if [[ ! -f "$src" ]]; then
        warn "Template missing: ${src}; skipping ${dest}"
        return
    fi
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
    info "Created ${dest} from ${src}"
}

setup_configs() {
    mkdir -p data/caddy/data data/caddy/config data/cloudflared \
      data/qbt-events data/qbt-offload data/seerr-request-reconciler
    # Docker creates missing bind-mount dirs as root; ensure worker-writable before first up.
    if [[ -n "${PUID:-}" && -n "${PGID:-}" ]]; then
        chown "${PUID}:${PGID}" data/qbt-events data/qbt-offload data/seerr-request-reconciler 2>/dev/null || true
    elif [[ "$(id -u)" -eq 0 ]]; then
        chown 1000:1000 data/qbt-events data/qbt-offload data/seerr-request-reconciler 2>/dev/null || true
    fi
    info "Ensured required data directories exist"

    local cloudflared_template cloudflared_path
    cloudflared_template="$(get_env_value "CLOUDFLARED_CONFIG_TEMPLATE" ".env")"
    cloudflared_path="$(get_env_value "CLOUDFLARED_CONFIG_PATH" ".env")"

    cloudflared_template="${cloudflared_template:-./config/cloudflared/config.yml.example}"
    cloudflared_path="${cloudflared_path:-./data/cloudflared/config.yml}"

    copy_template_if_missing "$cloudflared_template" "$cloudflared_path"
}

setup_gpu() {
    # GPU configuration is part of the default compose stack now.
    # We keep detection only as an informative message.
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        info "NVIDIA GPU detected"
    else
        warn "No NVIDIA GPU detected (Plex/Jellyfin/Ollama may fail if GPU is required)"
    fi
}

setup_network() {
    if ! command -v docker &>/dev/null; then
        warn "Docker not found; skipping homelab_net creation"
        return
    fi
    if docker network inspect homelab_net >/dev/null 2>&1; then
        info "Docker network homelab_net already exists"
    else
        docker network create homelab_net >/dev/null
        info "Created Docker network homelab_net"
    fi
}

validate_compose() {
    if ! command -v docker &>/dev/null; then
        warn "Docker not found; skipping compose validation"
        return 0
    fi

    local failed=0
    local bootstrap="${HOMELAB_INFISICAL_BOOTSTRAP:-${HOME}/.config/homelab/infisical.env}"
    local wrapper="${REPO_ROOT}/scripts/ops/homelab-compose.sh"

    if [[ -x "${wrapper}" && -r "${bootstrap}" ]]; then
        if "${wrapper}" config --quiet >/dev/null 2>&1; then
            info "Compose validation passed (homelab-compose.sh / Infisical)"
        else
            error "Compose validation failed (homelab-compose.sh / Infisical)"
            failed=1
        fi
    else
        # No Infisical bootstrap yet — CI-style placeholder validation.
        # shellcheck source=scripts/lib/managed-secrets.sh disable=SC1091
        source "${REPO_ROOT}/scripts/lib/managed-secrets.sh"
        export_ci_secret_placeholders
        if docker compose -f docker-compose.yml config --quiet >/dev/null 2>&1; then
            info "Compose validation passed (CI placeholders; Infisical bootstrap absent)"
        else
            error "Compose validation failed (CI placeholders)"
            failed=1
        fi
    fi

    return "$failed"
}

print_next_steps() {
    echo
    info "Next steps:"
    echo "  ./scripts/ops/homelab-compose.py up -d   # runtime host with Infisical bootstrap"
    echo "  # Without Infisical: ensure ~/.config/homelab/infisical.env exists first"
    echo "  # LLM stack: edit compose/base.yml and remove the llm.yml include if you do not want it"
    echo
}

run_hardening() {
    info "Running hardening steps"

    local perms_script="${REPO_ROOT}/scripts/hardening/secure-secret-file-permissions.sh"
    if [[ -f "$perms_script" ]]; then
        bash "$perms_script"
        info "Secret file permissions tightened"
    else
        warn "Missing ${perms_script}; skipping permission hardening"
    fi

    local nft_rules="${REPO_ROOT}/scripts/hardening/nftables-arr-stack.nft"
    if [[ -f "$nft_rules" ]]; then
        if command -v nft &>/dev/null; then
            sudo nft -f "$nft_rules"
            info "nftables rules loaded from ${nft_rules}"
        else
            warn "nft not found; skipping firewall rules"
        fi
    else
        warn "Missing ${nft_rules}; skipping firewall rules"
    fi
}

main() {
    local harden=0
    for arg in "$@"; do
        case "$arg" in
            --harden) harden=1 ;;
            *) warn "Unknown argument: $arg" ;;
        esac
    done

    info "Starting homelab setup in ${REPO_ROOT}"
    setup_env
    setup_configs
    setup_gpu
    setup_network
    validate_compose

    if [[ "$harden" -eq 1 ]]; then
        run_hardening
    fi

    print_next_steps
    info "Setup complete"
}

main "$@"
