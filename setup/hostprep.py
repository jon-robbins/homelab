"""Host preparation for local setup: refuse an unsuitable host, install Docker from its apt repository, set log limits.

Downloaded bytes are never used before their digest is checked.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Mapping
from urllib.request import urlopen

# The whole key file is pinned, so a file carrying extra keys is refused (apt would trust them all).
DOCKER_GPG_URL = "https://download.docker.com/linux/ubuntu/gpg"
DOCKER_GPG_SHA256 = "1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570"
DOCKER_SOURCE = (
    "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\n"
    "Components: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\n"
)
DOCKER_PACKAGES = ["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"]


def verify_digest(content: bytes, expected: str) -> bytes:
    actual = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise ValueError("download checksum mismatch")
    return content


def _matches(path: Path, content: bytes, mode: int) -> bool:
    try:
        return path.read_bytes() == content and stat.S_IMODE(path.stat().st_mode) == mode
    except FileNotFoundError:
        return False


def replace_file(destination: Path, content: bytes, mode: int) -> bool:
    """Write atomically. Returns False, touching nothing, when the file already has this content and mode."""
    if _matches(destination, content, mode):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


def merge_docker_config(path: Path) -> bool:
    """Add missing log limits, keeping everything else. Returns whether the file changed.

    Only json-file and local accept max-size/max-file; dockerd refuses to start if another driver gets them.
    """
    original = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    config = {"log-driver": "json-file", **original}
    if config["log-driver"] not in ("json-file", "local"):
        return False
    config["log-opts"] = {"max-size": "10m", "max-file": "3", **original.get("log-opts", {})}
    if path.exists() and config == original:
        return False
    replace_file(path, (json.dumps(config, indent=2, sort_keys=True) + "\n").encode(), 0o644)
    return True


def _run(command: list[str]) -> None:
    subprocess.run(
        command, check=True, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "a"}
    )


def _package_installed(package: str) -> bool:
    return subprocess.run(["dpkg-query", "-W", package], capture_output=True).returncode == 0


def install_docker() -> None:
    """Docker Engine and Compose from Docker's apt repository. A host that already has them is left alone."""
    if all(_package_installed(name) for name in DOCKER_PACKAGES):
        return
    with urlopen(DOCKER_GPG_URL, timeout=30) as response:
        replace_file(Path("/etc/apt/keyrings/docker.asc"), verify_digest(response.read(), DOCKER_GPG_SHA256), 0o644)
    replace_file(Path("/etc/apt/sources.list.d/docker.sources"), DOCKER_SOURCE.encode(), 0o644)
    _run(["apt-get", "update"])
    _run(["apt-get", "install", "-y", *DOCKER_PACKAGES])


def apply_local_host_config(root: Path = Path("/"), run: Callable[[list[str]], None] = _run) -> None:
    """Set Docker log limits. An unchanged file triggers nothing; a change restarts a running Docker only."""
    if merge_docker_config(root / "etc/docker/daemon.json"):
        run(["systemctl", "try-restart", "docker"])


def validate_target(os_release: Mapping[str, str], machine: str, memory_mib: int, public_ip: bool) -> None:
    if os_release.get("ID") != "ubuntu" or os_release.get("VERSION_ID") != "24.04" or machine != "x86_64":
        raise ValueError("only Ubuntu 24.04 amd64 is supported")
    if memory_mib < 4096:
        raise ValueError("at least 4096 MiB RAM is required")
    if public_ip:
        raise ValueError("a public IPv4 address is on this host; setup refuses a host that sits on the internet")


def _public_families(ip_json: str) -> set[int]:
    """IP versions (4, 6) for which `ip -j address` lists a globally routable address."""
    infos = (info for interface in json.loads(ip_json) for info in interface.get("addr_info", ()))
    addresses = (ipaddress.ip_address(info["local"]) for info in infos)
    return {address.version for address in addresses if address.is_global}


def _listening_web_ports() -> set[str]:
    rows = subprocess.run(["ss", "-H", "-ltn"], capture_output=True, text=True, check=True).stdout.splitlines()
    return {row.split()[3].rsplit(":", 1)[-1] for row in rows if len(row.split()) > 3} & {"80", "443"}


def preflight(*, check_ports: bool = True) -> None:
    """Refuse an unsuitable host before anything is written. A host setup already serves skips the port check."""
    if os.geteuid() != 0:
        raise ValueError("run local host preparation as root")
    addresses = subprocess.run(["ip", "-j", "address"], capture_output=True, text=True, check=True).stdout
    public = _public_families(addresses)
    memory_mib = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 1024**2
    validate_target(platform.freedesktop_os_release(), platform.machine(), memory_mib, 4 in public)
    if 6 in public:
        # Home ISPs give every host a global IPv6 address; the router's firewall, not the address, keeps it private.
        # Published ports bind loopback only, so this exposes host services such as sshd, never the media stack.
        print("note: this host has a public IPv6 address; check that your router blocks inbound IPv6", file=sys.stderr)
    if shutil.disk_usage("/").free < 2 * 1024**3:
        raise ValueError("less than 2 GiB is free on the root filesystem")
    if check_ports and (busy := sorted(_listening_web_ports())):
        raise ValueError(f"required web ports are already in use: {', '.join(busy)}")
