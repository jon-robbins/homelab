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
# Kept in step with setup/pins.json by a test.
COSIGN_URL=https://github.com/sigstore/cosign/releases/download/v3.1.3/cosign-linux-amd64
COSIGN_SHA256=4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71

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
  curl -fsSL --proto '=https,file' --retry 3 --max-time 1800 -C - -o "$partial" "$1"
  echo "$2  $partial" | sha256sum -c --quiet - || die "checksum mismatch for $1; the release was not installed"
  mv "$partial" "$3"
}

install_cosign() {
  if [[ -x /usr/local/bin/cosign ]] && echo "$COSIGN_SHA256  /usr/local/bin/cosign" | sha256sum -c --quiet - 2>/dev/null; then
    return
  fi
  fetch "$COSIGN_URL" "$COSIGN_SHA256" "$work/cosign"
  install -m 0755 "$work/cosign" /usr/local/bin/cosign
}

# WS-13 SEAM: replace the digest check with `cosign verify-blob --bundle` against the release workflow identity
# (setup/hostprep.py verify_signed_release holds the identity). Until then an archive is trusted only by its digest.
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

make_venv() {
  local venv=$INSTALL_DIR/venv
  # Test pip, not python: a venv that failed for lack of python3-venv still leaves bin/python behind.
  if [[ ! -x $venv/bin/pip ]]; then
    python3 -m venv --clear "$venv" 2>/dev/null || {
      apt-get update -qq >&2
      apt-get install -y python3-venv >&2
      python3 -m venv --clear "$venv"
    }
  fi
  "$venv/bin/pip" install --quiet --disable-pip-version-check --only-binary=:all: --require-hashes -r "$platform/config/release/requirements.txt"
}

check_host
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
install_cosign
fetch_release
make_venv
cd "$platform"
export PYTHONDONTWRITEBYTECODE=1
"$INSTALL_DIR/venv/bin/python" -m setup.cli --platform "$platform" "$@" start
