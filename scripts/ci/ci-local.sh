#!/usr/bin/env bash
# Compatibility entrypoint for callers of the retired local CI command.
# The source of truth is now pre-commit plus nox.
set -euo pipefail

scope="${1:-validate}"
if [[ "$scope" != "validate" ]]; then
  echo "FATAL: only the validate compatibility scope is supported" >&2
  exit 2
fi

for argument in "${@:2}"; do
  [[ "$argument" == "--no-docker" ]] || {
    echo "FATAL: unsupported argument '$argument'" >&2
    exit 2
  }
done

if command -v pre-commit >/dev/null 2>&1 && command -v nox >/dev/null 2>&1; then
  pre-commit run --all-files
  nox -t no-docker
elif command -v uv >/dev/null 2>&1; then
  gitleaks_version="8.28.0"
  gitleaks_bin_dir="${TMPDIR:-/tmp}/homelab-next-gitleaks-${gitleaks_version}"
  export UV_CACHE_DIR="${TMPDIR:-/tmp}/homelab-next-uv-cache"
  mkdir -p "$UV_CACHE_DIR"
  if ! command -v gitleaks >/dev/null 2>&1; then
    mkdir -p "$gitleaks_bin_dir"
    if [[ ! -x "$gitleaks_bin_dir/gitleaks" ]]; then
      case "$(uname -s):$(uname -m)" in
        Linux:x86_64) archive="gitleaks_${gitleaks_version}_linux_x64.tar.gz" ;;
        Linux:aarch64) archive="gitleaks_${gitleaks_version}_linux_arm64.tar.gz" ;;
        Darwin:x86_64) archive="gitleaks_${gitleaks_version}_darwin_x64.tar.gz" ;;
        Darwin:arm64) archive="gitleaks_${gitleaks_version}_darwin_arm64.tar.gz" ;;
        *) echo "FATAL: unsupported gitleaks platform $(uname -s)/$(uname -m)" >&2; exit 1 ;;
      esac
      curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${gitleaks_version}/${archive}" \
        | tar -xz -C "$gitleaks_bin_dir" gitleaks
    fi
    export PATH="$gitleaks_bin_dir:$PATH"
  fi
  uv run --with pre-commit==4.2.0 --with nox==2025.2.9 pre-commit run --all-files
  uv run --with nox==2025.2.9 nox -t no-docker
else
  echo "FATAL: install pinned pre-commit and nox (or uv)" >&2
  exit 1
fi
