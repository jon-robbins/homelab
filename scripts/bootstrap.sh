#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${ROOT}/bin"

die() { echo "bootstrap: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

require_not_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    die "do not run as root. Run as your normal user (sudo will be requested only if needed)."
  fi
}

ensure_dirs() {
  mkdir -p "$BIN_DIR" "${ROOT}/data/cloudflared" "${ROOT}/data/cloudflared/credentials"
}

platform_arch() {
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) echo "x86_64" ;;
    aarch64|arm64) echo "arm64" ;;
    *)
      die "unsupported arch: ${arch} (expected amd64/arm64)"
      ;;
  esac
}

gum_bin() {
  if [[ -x "${BIN_DIR}/gum" ]]; then
    echo "${BIN_DIR}/gum"
  else
    echo "gum"
  fi
}

ensure_gum() {
  if have gum || [[ -x "${BIN_DIR}/gum" ]]; then
    return 0
  fi

  echo "bootstrap: gum not found; installing locally to ${BIN_DIR}/gum" >&2
  local arch tmp url ver tar
  arch="$(platform_arch)"
  ver="${GUM_VERSION:-0.14.5}"
  tar="gum_${ver}_Linux_${arch}.tar.gz"
  url="https://github.com/charmbracelet/gum/releases/download/v${ver}/${tar}"
  tmp="$(mktemp -d)"
  trap "rm -rf '$tmp'" EXIT

  if ! have curl && ! have wget; then
    die "need curl or wget to install gum"
  fi
  if have curl; then
    curl -fsSL "$url" -o "${tmp}/${tar}"
  else
    wget -qO "${tmp}/${tar}" "$url"
  fi

  tar -xzf "${tmp}/${tar}" -C "$tmp"
  install -m 0755 "${tmp}/gum_${ver}_Linux_${arch}/gum" "${BIN_DIR}/gum"
}

cloudflared_bin() {
  if [[ -x "${BIN_DIR}/cloudflared" ]]; then
    echo "${BIN_DIR}/cloudflared"
  else
    echo "cloudflared"
  fi
}

ensure_cloudflared() {
  if have cloudflared || [[ -x "${BIN_DIR}/cloudflared" ]]; then
    return 0
  fi

  echo "bootstrap: cloudflared not found; installing locally to ${BIN_DIR}/cloudflared" >&2
  local arch url tmp
  arch="$(platform_arch)"
  case "$arch" in
    x86_64) url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" ;;
    arm64) url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64" ;;
  esac
  tmp="$(mktemp -d)"
  trap "rm -rf '$tmp'" EXIT

  if ! have curl && ! have wget; then
    die "need curl or wget to install cloudflared"
  fi
  if have curl; then
    curl -fsSL "$url" -o "${tmp}/cloudflared"
  else
    wget -qO "${tmp}/cloudflared" "$url"
  fi
  install -m 0755 "${tmp}/cloudflared" "${BIN_DIR}/cloudflared"
}

prompt() {
  local g; g="$(gum_bin)"
  "$g" input --prompt "$1" --value "${2:-}"
}

choose() {
  local g; g="$(gum_bin)"
  "$g" choose "$@"
}

confirm() {
  local g; g="$(gum_bin)"
  if [[ ! -t 0 ]]; then
    # Non-interactive safety: default to "no" instead of looping.
    return 1
  fi
  "$g" confirm "$1"
}

style_header() {
  local g; g="$(gum_bin)"
  "$g" style --border normal --margin "1 0" --padding "1 2" --bold --foreground 212 "$1"
}

write_env() {
  local puid pgid tz env_file tmp
  puid="$1"; pgid="$2"; tz="$3"
  env_file="${ROOT}/.env"
  tmp="$(mktemp)"

  if [[ -f "$env_file" ]]; then
    awk '
      {
        line = $0
        if (line ~ /^[[:space:]]*#/) { print; next }
        if (line ~ /^[[:space:]]*$/) { print; next }

        sub(/^[[:space:]]*export[[:space:]]+/, "", line)
        split(line, parts, "=")
        key = parts[1]
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)

        if (key == "PUID" || key == "PGID" || key == "TZ") next
        print
      }
    ' "$env_file" > "$tmp"
  else
    : > "$tmp"
  fi

  {
    cat <<EOF
PUID=${puid}
PGID=${pgid}
TZ=${tz}
EOF
    if [[ -s "$tmp" ]]; then
      printf '\n'
      cat "$tmp"
    fi
  } > "$env_file"

  rm -f "$tmp"
}

detect_tz() {
  if have timedatectl; then
    timedatectl show -p Timezone --value 2>/dev/null || true
  elif [[ -f /etc/timezone ]]; then
    cat /etc/timezone 2>/dev/null || true
  else
    echo ""
  fi
}

rewrite_compose() {
  local media_hdd media_nvme plex_srv jellyfin_url enable_gpu
  media_hdd="$1"
  media_nvme="$2"
  plex_srv="$3"
  jellyfin_url="$4"
  enable_gpu="$5"

  local args=( "--repo-root" "${ROOT}"
               "--media-hdd" "${media_hdd}"
               "--media-nvme" "${media_nvme}"
               "--plex-srv" "${plex_srv}"
               "--jellyfin-url" "${jellyfin_url}" )
  if [[ "$enable_gpu" == "yes" ]]; then
    args+=( "--enable-gpu" )
  fi

  python3 "${ROOT}/scripts/rewrite_compose.py" "${args[@]}"
}

ensure_homelab_net() {
  if docker network inspect homelab_net >/dev/null 2>&1; then
    return 0
  fi
  docker network create homelab_net >/dev/null
}

cloudflare_setup() {
  local g; g="$(gum_bin)"
  local cf; cf="$(cloudflared_bin)"

  style_header "Cloudflare Tunnel setup"
  echo "This will run: tunnel login (browser), tunnel create, and DNS routes."

  local tunnel_name
  tunnel_name="$(prompt "Tunnel name: " "homelab")"
  [[ -n "$tunnel_name" ]] || die "tunnel name required"

  "$g" spin --title "Cloudflare login..." -- "$cf" tunnel login

  # Create tunnel; if it already exists, continue.
  if ! "$cf" tunnel create "$tunnel_name" >/dev/null 2>&1; then
    echo "bootstrap: tunnel create failed (may already exist). Continuing." >&2
  fi

  # Resolve UUID by name from 'cloudflared tunnel list'.
  local uuid
  uuid="$("$cf" tunnel list 2>/dev/null | awk -v n="$tunnel_name" '$2==n{print $1; exit}')" || true
  [[ -n "${uuid:-}" ]] || die "could not determine tunnel UUID for name: ${tunnel_name}"

  local base_domain host_get host_stream host_jf
  base_domain="$(prompt "Base domain (e.g. example.com): " "")"
  [[ -n "$base_domain" ]] || die "base domain required"

  host_get="$(prompt "Overseerr hostname: " "get.${base_domain}")"
  host_stream="$(prompt "Plex hostname: " "stream.${base_domain}")"
  host_jf="$(prompt "Jellyfin hostname: " "jf.${base_domain}")"

  # Create config.yml from example.
  local cfg_example="${ROOT}/data/cloudflared/config.yml.example"
  local cfg="${ROOT}/data/cloudflared/config.yml"
  [[ -f "$cfg_example" ]] || die "missing ${cfg_example}"
  if [[ -f "$cfg" ]]; then
    if ! confirm "data/cloudflared/config.yml exists. Overwrite it?"; then
      echo "bootstrap: keeping existing config.yml" >&2
    else
      rm -f "$cfg"
    fi
  fi
  if [[ ! -f "$cfg" ]]; then
    sed \
      -e "s/REPLACE_WITH_TUNNEL_UUID/${uuid}/g" \
      -e "s/seerr\\.example\\.com/${host_get}/g" \
      -e "s/prowl\\.example\\.com/prowlarr.${base_domain}/g" \
      -e "s/stream\\.example\\.com/${host_stream}/g" \
      "$cfg_example" > "$cfg"
  fi

  # Ensure creds exist (created by create/login). We won't try to guess path locations; we just check.
  if [[ ! -f "${ROOT}/data/cloudflared/credentials/${uuid}.json" ]]; then
    echo "bootstrap: expected credentials JSON missing at data/cloudflared/credentials/${uuid}.json" >&2
    echo "bootstrap: if cloudflared wrote it elsewhere, copy it into that path." >&2
  fi

  # DNS routes
  "$g" spin --title "Creating DNS routes..." -- "$cf" tunnel route dns "$tunnel_name" "$host_get" || true
  "$g" spin --title "Creating DNS routes..." -- "$cf" tunnel route dns "$tunnel_name" "$host_stream" || true
  "$g" spin --title "Creating DNS routes..." -- "$cf" tunnel route dns "$tunnel_name" "$host_jf" || true

  echo "$base_domain"
}

main() {
  require_not_root
  ensure_dirs

  if ! have docker; then
    die "docker not found"
  fi
  if ! docker compose version >/dev/null 2>&1; then
    die "docker compose plugin not found (try installing docker compose v2)"
  fi
  if ! have python3; then
    die "python3 not found"
  fi

  ensure_gum

  local g; g="$(gum_bin)"

  style_header "Homelab bootstrap wizard"
  echo "This will rewrite compose files and create local config files."
  echo
  if ! confirm "Continue?"; then
    exit 0
  fi

  # Identity
  local default_puid default_pgid default_tz
  default_puid="$(id -u)"
  default_pgid="$(id -g)"
  default_tz="$(detect_tz)"
  [[ -n "$default_tz" ]] || default_tz="UTC"

  style_header "User and timezone"
  local puid pgid tz
  puid="$(prompt "PUID: " "$default_puid")"
  pgid="$(prompt "PGID: " "$default_pgid")"
  tz="$(prompt "Timezone (TZ): " "$default_tz")"
  [[ -n "$puid" && -n "$pgid" && -n "$tz" ]] || die "PUID/PGID/TZ required"
  write_env "$puid" "$pgid" "$tz"

  # Paths
  style_header "Media paths"
  echo "These replace the default hard-coded mounts."
  local media_hdd media_nvme plex_srv
  media_hdd="$(prompt "Media HDD root (replaces /mnt/media-hdd): " "/mnt/media-hdd")"
  media_nvme="$(prompt "Media NVMe root (replaces /mnt/media-nvme): " "/mnt/media-nvme")"
  plex_srv="$(prompt "Plex service path (replaces /srv/plex): " "/srv/plex")"

  # GPU
  style_header "GPU"
  local has_nvidia gpu_choice enable_gpu
  if have nvidia-smi && nvidia-smi >/dev/null 2>&1; then
    has_nvidia="yes"
  else
    has_nvidia="no"
  fi
  if [[ "$has_nvidia" == "yes" ]]; then
    gpu_choice="$(choose --header "NVIDIA GPU detected. Enable GPU support in compose?" "Enable GPU" "Disable GPU")"
  else
    gpu_choice="$(choose --header "No NVIDIA GPU detected. GPU support will be disabled by default." "Disable GPU" "Enable GPU anyway")"
  fi
  if [[ "$gpu_choice" == "Enable GPU"* ]]; then
    enable_gpu="yes"
  else
    enable_gpu="no"
  fi

  # Domain + Jellyfin URL + Cloudflare
  style_header "Remote access (Cloudflare Tunnel)"
  ensure_cloudflared

  local base_domain
  base_domain="$(cloudflare_setup)"
  local jellyfin_url="https://jf.${base_domain}"

  # Rewrite compose
  style_header "Apply compose rewrites"
  "$g" spin --title "Rewriting compose files..." -- \
    bash -lc "python3 \"${ROOT}/scripts/rewrite_compose.py\" --repo-root \"${ROOT}\" --media-hdd \"${media_hdd}\" --media-nvme \"${media_nvme}\" --plex-srv \"${plex_srv}\" --jellyfin-url \"${jellyfin_url}\" $( [[ \"$enable_gpu\" == \"yes\" ]] && echo --enable-gpu )"

  # Network
  style_header "Docker network"
  if confirm "Create docker network 'homelab_net' now?"; then
    "$g" spin --title "Creating homelab_net..." -- ensure_homelab_net
  else
    echo "Skipping network creation."
  fi

  # Validate
  style_header "Validate compose"
  "$g" spin --title "docker-compose.network.yml ..." -- docker compose -f "${ROOT}/docker-compose.network.yml" config >/dev/null
  "$g" spin --title "docker-compose.media.yml ..." -- docker compose -f "${ROOT}/docker-compose.media.yml" config >/dev/null
  "$g" spin --title "docker-compose.llm.yml ..." -- docker compose -f "${ROOT}/docker-compose.llm.yml" config >/dev/null

  style_header "Next steps"
  echo "Run:"
  echo "  docker compose -f docker-compose.network.yml up -d"
  echo "  docker compose -f docker-compose.media.yml up -d"
  echo "  docker compose -f docker-compose.llm.yml up -d"
  echo
  echo "Secrets were written locally to:"
  echo "  data/cloudflared/config.yml"
  echo "  data/cloudflared/credentials/"
  echo "(They are intentionally untracked.)"
}

main "$@"

