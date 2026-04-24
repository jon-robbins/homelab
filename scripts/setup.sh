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
    mkdir -p data/caddy/data data/caddy/config data/cloudflared data/dashy
    info "Ensured required data directories exist"

    local dashy_template cloudflared_template dashy_path cloudflared_path
    dashy_template="$(get_env_value "DASHY_CONFIG_TEMPLATE" ".env")"
    cloudflared_template="$(get_env_value "CLOUDFLARED_CONFIG_TEMPLATE" ".env")"
    dashy_path="$(get_env_value "DASHY_CONFIG_PATH" ".env")"
    cloudflared_path="$(get_env_value "CLOUDFLARED_CONFIG_PATH" ".env")"

    dashy_template="${dashy_template:-./config/dashy/conf.yml.example}"
    cloudflared_template="${cloudflared_template:-./config/cloudflared/config.yml.example}"
    dashy_path="${dashy_path:-./data/dashy/conf.yml}"
    cloudflared_path="${cloudflared_path:-./data/cloudflared/config.yml}"

    copy_template_if_missing "$dashy_template" "$dashy_path"
    copy_template_if_missing "$cloudflared_template" "$cloudflared_path"
}

GPU_ENABLED=0

setup_gpu() {
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        info "NVIDIA GPU detected"
        if [[ -t 0 ]] && confirm "Enable GPU acceleration for Plex, Jellyfin, and Ollama?"; then
            cp config/gpu/docker-compose.gpu.yml docker-compose.gpu.yml
            GPU_ENABLED=1
            info "GPU overlay enabled at docker-compose.gpu.yml"
        else
            rm -f docker-compose.gpu.yml
            info "GPU overlay not enabled"
        fi
    else
        warn "No NVIDIA GPU detected, skipping GPU configuration"
        rm -f docker-compose.gpu.yml
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
        info "Created external Docker network homelab_net"
    fi
}

validate_compose() {
    if ! command -v docker &>/dev/null; then
        warn "Docker not found; skipping compose validation"
        return 0
    fi

    local files=(
        "docker-compose.network.yml"
        "docker-compose.media.yml"
        "docker-compose.llm.yml"
    )
    local file failed=0

    for file in "${files[@]}"; do
        if [[ ! -f "$file" ]]; then
            warn "Missing compose file: ${file}"
            continue
        fi
        if docker compose -f "$file" config --quiet >/dev/null 2>&1; then
            info "Compose validation passed: ${file}"
        else
            error "Compose validation failed: ${file}"
            failed=1
        fi
    done

    if [[ -f docker-compose.gpu.yml ]]; then
        if docker compose \
            -f docker-compose.network.yml \
            -f docker-compose.media.yml \
            -f docker-compose.llm.yml \
            -f docker-compose.gpu.yml \
            config --quiet >/dev/null 2>&1; then
            info "Compose validation passed: docker-compose.gpu.yml (overlay stack)"
        else
            error "Compose validation failed: docker-compose.gpu.yml (overlay stack)"
            failed=1
        fi
    fi

    return "$failed"
}

print_next_steps() {
    local media_cmd llm_cmd
    media_cmd="docker compose -f docker-compose.media.yml"
    llm_cmd="docker compose -f docker-compose.llm.yml"
    if [[ "$GPU_ENABLED" -eq 1 && -f docker-compose.gpu.yml ]]; then
        media_cmd="${media_cmd} -f docker-compose.gpu.yml"
        llm_cmd="${llm_cmd} -f docker-compose.gpu.yml"
    fi

    echo
    info "Next steps:"
    echo "  docker compose -f docker-compose.network.yml up -d"
    echo "  ${media_cmd} up -d"
    echo "  ${llm_cmd} up -d  # optional"
    echo
}

main() {
    info "Starting homelab setup in ${REPO_ROOT}"
    setup_env
    setup_configs
    setup_gpu
    setup_network
    validate_compose
    print_next_steps
    info "Setup complete"
}

main "$@"
