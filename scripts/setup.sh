#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
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

ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE_FILE="${REPO_ROOT}/.env.example"
GPU_OVERLAY_FILE="${REPO_ROOT}/docker-compose.gpu.yml"
GPU_OVERLAY_ENABLED=0

require_command() {
    local command_name="$1"
    local help_text="${2:-}"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        error "Required command not found: ${command_name}"
        if [[ -n "$help_text" ]]; then
            warn "$help_text"
        fi
        exit 1
    fi
}

escape_sed_replacement() {
    printf '%s' "$1" | sed 's/[\/&]/\\&/g'
}

get_env_value() {
    local key="$1"
    if [[ ! -f "$ENV_FILE" ]]; then
        return 0
    fi

    awk -F= -v key="$key" '
        $0 ~ "^[[:space:]]*" key "=" {
            sub(/^[^=]*=/, "", $0)
            print $0
            exit
        }
    ' "$ENV_FILE"
}

set_env_value() {
    local key="$1"
    local value="$2"
    local escaped_value
    escaped_value="$(escape_sed_replacement "$value")"

    if grep -qE "^[[:space:]]*${key}=" "$ENV_FILE"; then
        sed -i "s|^[[:space:]]*${key}=.*|${key}=${escaped_value}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

prompt_env_value() {
    local key="$1"
    local prompt_text="$2"
    local default_value="$3"
    local response

    if [[ -t 0 ]]; then
        read -r -p "${prompt_text} [${default_value}]: " response
        if [[ -z "$response" ]]; then
            response="$default_value"
        fi
    else
        response="$default_value"
        warn "Non-interactive shell: using ${key}=${response}"
    fi

    set_env_value "$key" "$response"
}

copy_template_if_missing() {
    local source_file="$1"
    local destination_file="$2"

    if [[ ! -f "$source_file" ]]; then
        warn "Template not found: ${source_file} (skipping)"
        return 0
    fi

    if [[ -f "$destination_file" ]]; then
        info "Keeping existing file: ${destination_file}"
        return 0
    fi

    cp "$source_file" "$destination_file"
    info "Created from template: ${destination_file}"
}

setup_env() {
    info "Setting up .env"
    if [[ ! -f "$ENV_EXAMPLE_FILE" ]]; then
        error "Missing ${ENV_EXAMPLE_FILE}"
        exit 1
    fi

    if [[ ! -f "$ENV_FILE" ]]; then
        cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
        info "Copied .env.example to .env"
    else
        info ".env already exists, preserving current file"
    fi

    local media_hdd_default media_nvme_default plex_data_default
    media_hdd_default="$(get_env_value "MEDIA_HDD_PATH")"
    media_nvme_default="$(get_env_value "MEDIA_NVME_PATH")"
    plex_data_default="$(get_env_value "PLEX_DATA_PATH")"

    [[ -n "$media_hdd_default" ]] || media_hdd_default="/mnt/media-hdd"
    [[ -n "$media_nvme_default" ]] || media_nvme_default="/mnt/media-nvme"
    [[ -n "$plex_data_default" ]] || plex_data_default="/srv/plex"

    prompt_env_value "MEDIA_HDD_PATH" "Enter media HDD path" "$media_hdd_default"
    prompt_env_value "MEDIA_NVME_PATH" "Enter media NVME path" "$media_nvme_default"
    prompt_env_value "PLEX_DATA_PATH" "Enter Plex data path" "$plex_data_default"
}

setup_configs() {
    info "Creating required data directories"
    mkdir -p \
        "${REPO_ROOT}/data/caddy/data" \
        "${REPO_ROOT}/data/caddy/config" \
        "${REPO_ROOT}/data/cloudflared" \
        "${REPO_ROOT}/data/cloudflared/credentials" \
        "${REPO_ROOT}/data/dashy"

    copy_template_if_missing \
        "${REPO_ROOT}/config/dashy/conf.yml.example" \
        "${REPO_ROOT}/data/dashy/conf.yml"
    copy_template_if_missing \
        "${REPO_ROOT}/config/cloudflared/config.yml.example" \
        "${REPO_ROOT}/data/cloudflared/config.yml"
}

setup_gpu() {
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        info "NVIDIA GPU detected"
        if [[ -t 0 ]] && confirm "Enable GPU acceleration for Plex, Jellyfin, and Ollama?"; then
            if [[ -f "$GPU_OVERLAY_FILE" ]]; then
                info "Keeping existing GPU overlay: ${GPU_OVERLAY_FILE}"
            else
                cp "${REPO_ROOT}/config/gpu/docker-compose.gpu.yml" "$GPU_OVERLAY_FILE"
                info "Created GPU overlay: docker-compose.gpu.yml"
            fi
            GPU_OVERLAY_ENABLED=1
        else
            info "GPU overlay not enabled"
            rm -f "$GPU_OVERLAY_FILE"
        fi
    else
        warn "No NVIDIA GPU detected, skipping GPU configuration"
        rm -f "$GPU_OVERLAY_FILE"
    fi
}

setup_network() {
    info "Ensuring docker network homelab_net exists"
    if docker network inspect homelab_net >/dev/null 2>&1; then
        info "Docker network already present: homelab_net"
    else
        docker network create homelab_net >/dev/null
        info "Created docker network: homelab_net"
    fi
}

validate_compose_file() {
    local compose_file="$1"
    if docker compose -f "$compose_file" config --quiet >/dev/null 2>&1; then
        info "Validation passed: ${compose_file}"
        return 0
    fi

    error "Validation failed: ${compose_file}"
    return 1
}

validate_workflow() {
    local failures=0

    info "Validating compose files"
    validate_compose_file "${REPO_ROOT}/docker-compose.network.yml" || failures=$((failures + 1))
    validate_compose_file "${REPO_ROOT}/docker-compose.media.yml" || failures=$((failures + 1))
    validate_compose_file "${REPO_ROOT}/docker-compose.llm.yml" || failures=$((failures + 1))

    if [[ -f "$GPU_OVERLAY_FILE" ]]; then
        if docker compose \
            -f "${REPO_ROOT}/docker-compose.media.yml" \
            -f "$GPU_OVERLAY_FILE" \
            config --quiet >/dev/null 2>&1; then
            info "Validation passed: media + gpu overlay"
            GPU_OVERLAY_ENABLED=1
        else
            error "Validation failed: media + gpu overlay"
            failures=$((failures + 1))
        fi

        if docker compose \
            -f "${REPO_ROOT}/docker-compose.llm.yml" \
            -f "$GPU_OVERLAY_FILE" \
            config --quiet >/dev/null 2>&1; then
            info "Validation passed: llm + gpu overlay"
            GPU_OVERLAY_ENABLED=1
        else
            error "Validation failed: llm + gpu overlay"
            failures=$((failures + 1))
        fi
    fi

    if [[ "$failures" -gt 0 ]]; then
        error "Compose validation failed (${failures} checks)"
        exit 1
    fi
}

print_next_steps() {
    echo
    info "Next steps"
    if [[ "$GPU_OVERLAY_ENABLED" -eq 1 && -f "$GPU_OVERLAY_FILE" ]]; then
        echo "docker compose -f docker-compose.network.yml up -d"
        echo "docker compose -f docker-compose.media.yml -f docker-compose.gpu.yml up -d"
        echo "docker compose -f docker-compose.llm.yml -f docker-compose.gpu.yml up -d"
    else
        echo "docker compose -f docker-compose.network.yml up -d"
        echo "docker compose -f docker-compose.media.yml up -d"
        echo "docker compose -f docker-compose.llm.yml up -d"
    fi
}

main() {
    require_command "docker" "Install Docker Engine and ensure docker is on PATH."
    if ! docker compose version >/dev/null 2>&1; then
        error "docker compose plugin is required"
        exit 1
    fi

    setup_env
    setup_configs
    setup_gpu
    setup_network
    validate_workflow
    print_next_steps
}

main "$@"
