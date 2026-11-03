#!/usr/bin/env bash
# One-command bootstrap for an empty Ubuntu 24.04 amd64 host: run as root, from a file you downloaded and checked:
#
#   curl -fsSLO <release>/bootstrap.sh && echo "<sha256>  bootstrap.sh" | sha256sum -c && sudo bash bootstrap.sh
#
# It never runs downloaded bytes before checking them against a pinned digest, and it never pipes into a shell.
# Extra arguments go to `python3 -m setup.cli`. Running it again changes nothing.
set -euo pipefail

# --- Stamped when a release is cut (WS-13). A checkout carries no pin, so it refuses to fetch a release. ---
# Tests and local VMs may override both with HOMELAB_RELEASE_URL and HOMELAB_RELEASE_SHA256.
RELEASE_URL=${HOMELAB_RELEASE_URL:-}
RELEASE_SHA256=${HOMELAB_RELEASE_SHA256:-}

INSTALL_DIR=/opt/homelab
OWNER_POLICY=/srv/homelab/trust/policy.json

die() {
  echo "bootstrap: $*" >&2
  exit 1
}

check_host() {
  [[ $EUID -eq 0 ]] || die "run as root (sudo bash bootstrap.sh)"
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 && $(uname -m) == x86_64 ]] ||
    die "only Ubuntu 24.04 on amd64 is supported (this is ${PRETTY_NAME:-unknown} $(uname -m))"
  # The owner's signed-release pipeline and local setup never share a host; refuse before writing anything.
  [[ ! -e $OWNER_POLICY ]] || die "this host runs the signed-release pipeline ($OWNER_POLICY exists); use another host"
  [[ -n $RELEASE_URL && -n $RELEASE_SHA256 ]] ||
    die "this copy is not tied to a release (no pinned archive URL and digest); download bootstrap.sh from a release"
}

# fetch URL DIGEST DEST: download, verify the pinned digest, only then move into place.
fetch() {
  local partial
  partial=$(mktemp "$work/fetch.XXXXXX")
  curl -fsSL --proto '=https' --retry 3 --max-time 1800 -o "$partial" "$1"
  echo "$2  $partial" | sha256sum -c --quiet - || die "checksum mismatch for $1; the release was not installed"
  mv "$partial" "$3"
}

# WS-13 SEAM: add `cosign verify-blob --bundle` against the release workflow identity here, installing cosign
# first. Until then an archive is trusted only by its pinned digest.
fetch_release() {
  local target=$INSTALL_DIR/releases/$RELEASE_SHA256
  if [[ ! -d $target ]]; then
    fetch "$RELEASE_URL" "$RELEASE_SHA256" "$work/release.tar.gz"
    mkdir -p "$INSTALL_DIR/releases"
    # Unpack beside the target and rename: an interrupted copy never leaves a partial $target to trust next run.
    local staging
    rm -rf "$INSTALL_DIR"/releases/.staging.*  # left by an interrupted run
    staging=$(mktemp -d "$INSTALL_DIR/releases/.staging.XXXXXX")
    tar -xzf "$work/release.tar.gz" -C "$staging" --strip-components=1 --no-same-owner
    mv -T "$staging" "$target"
  fi
  platform=$target
}

# Setup needs only PyYAML beyond the standard library; Ubuntu's package is enough, so there is no virtualenv.
install_yaml() {
  dpkg -s python3-yaml >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y python3-yaml; } >&2
}

check_host
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fetch_release
install_yaml
cd "$platform"
export PYTHONDONTWRITEBYTECODE=1
python3 -m setup.cli --platform "$platform" "$@" prepare
