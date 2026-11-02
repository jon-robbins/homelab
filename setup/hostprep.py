"""Pinned bootstrap artifact retrieval. Never execute downloaded bytes before digest verification."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Mapping
from urllib.request import urlopen


def verify_digest(content: bytes, expected: str) -> bytes:
    actual = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise ValueError("download checksum mismatch")
    return content


def artifact_pin(name: str) -> Mapping[str, str]:
    pins = json.loads((Path(__file__).with_name("pins.json")).read_text(encoding="utf-8"))
    try:
        return pins[name]
    except KeyError as error:
        raise ValueError(f"no pinned bootstrap artifact named {name}") from error


def download_verified(url: str, expected: str, destination: Path) -> Path:
    with urlopen(url, timeout=30) as response:
        content = verify_digest(response.read(), expected)
    replace_file(destination, content, 0o755)
    return destination


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


def install_cosign(destination: Path) -> Path:
    pin = artifact_pin("cosign")
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == pin["sha256"]:
        return destination
    return download_verified(pin["url"], pin["sha256"], destination)


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


DOCKER_PACKAGES = [
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-buildx-plugin",
    "docker-compose-plugin",
]
UBUNTU_PACKAGES = ["restic", "sqlite3", "python3-venv"]
ALL_PACKAGES = [*DOCKER_PACKAGES, *UBUNTU_PACKAGES]


def _package_installed(package: str) -> bool:
    return subprocess.run(["dpkg-query", "-W", package], capture_output=True).returncode == 0


def _install_docker_key(destination: Path) -> None:
    """The whole key file is pinned, so a file carrying extra keys is refused (apt would trust them all)."""
    pin = artifact_pin("docker")
    replace_file(destination, verify_digest(urlopen(pin["key_url"], timeout=30).read(), pin["key_sha256"]), 0o644)


def _install_apt_packages() -> None:
    if all(_package_installed(name) for name in ALL_PACKAGES):
        return
    _run(["apt-get", "update"])
    _run(["apt-get", "install", "-y", "ca-certificates", "curl"])
    _install_docker_key(Path("/etc/apt/keyrings/docker.asc"))
    source = (
        "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\n"
        "Components: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\n"
    )
    replace_file(Path("/etc/apt/sources.list.d/docker.sources"), source.encode(), 0o644)
    _run(["apt-get", "update"])
    _run(["apt-get", "install", "-y", *ALL_PACKAGES])


def _install_infisical() -> None:
    pin = artifact_pin("infisical")
    if _package_installed("infisical"):
        return
    with tempfile.TemporaryDirectory() as temporary:
        os.chmod(temporary, 0o755)  # apt downloads as an unprivileged user
        _run(["apt-get", "install", "-y", str(download_verified(pin["url"], pin["sha256"], Path(temporary) / "i.deb"))])


def install_pinned_packages() -> None:
    """Install host dependencies. Anything already installed is left alone, so a rerun changes nothing."""
    install_cosign(Path("/usr/local/bin/cosign"))
    _install_apt_packages()
    _install_infisical()


def ensure_service_user() -> None:
    if subprocess.run(["getent", "group", "homelab"], capture_output=True).returncode:
        _run(["groupadd", "--system", "homelab"])
    if subprocess.run(["getent", "passwd", "homelab-secrets"], capture_output=True).returncode:
        _run(
            [
                "useradd",
                "--system",
                "--no-create-home",
                "--home-dir",
                "/nonexistent",
                "--shell",
                "/usr/sbin/nologin",
                "--gid",
                "homelab",
                "homelab-secrets",
            ]
        )
    groups = subprocess.run(["id", "-nG", "homelab-secrets"], capture_output=True, text=True, check=True).stdout.split()
    if "docker" in groups:
        raise ValueError("homelab-secrets must not be a member of the Docker group")


def wait_for_secrets(config: Path, secret_dir: Path, timeout_s: float | None = None, poll_s: float = 1) -> None:
    """Block until every secret the agent config renders exists. With no timeout it waits for as long as it takes."""
    if not config.is_file():
        raise ValueError("Infisical agent config is missing")
    names = sorted(set(re.findall(r"/run/homelab-secrets/([A-Z0-9_]+)", config.read_text(encoding="utf-8"))))
    if not names:
        raise ValueError("Infisical agent config has no rendered secret destinations")
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while missing := [n for n in names if not (secret_dir / n).is_file() or (secret_dir / n).stat().st_size == 0]:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"required secrets are not rendered: {', '.join(missing)}")
        time.sleep(poll_s)


AGENT_UNIT = """[Unit]
Description=Homelab Infisical Agent (local setup)
After=network-online.target
Wants=network-online.target
# Nothing to wait for until a secrets provider has been wired. From then on a missing secret keeps Docker down.
ConditionPathExists=/etc/infisical-agent/config.yaml

[Service]
Type=simple
User=homelab-secrets
Group=homelab
RuntimeDirectory=homelab-secrets
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
ExecStart=/usr/bin/infisical agent --config /etc/infisical-agent/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
# Docker requires this one-shot gate, not the agent: restarting the agent (a provider change) must not restart Docker.
READY_UNIT = """[Unit]
Description=Wait until every required homelab secret file exists
Wants=homelab-infisical-agent.service
After=homelab-infisical-agent.service
ConditionPathExists=/etc/infisical-agent/config.yaml

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/libexec/homelab-wait-for-secrets
# A secret that shows up late (a router slower than the host) still starts Docker.
TimeoutStartSec=infinity

[Install]
WantedBy=multi-user.target
"""
DOCKER_DROPIN = """[Unit]
# Only a missing secret holds Docker back.
Requires=homelab-secrets-ready.service
After=homelab-secrets-ready.service
"""
WAIT_HELPER = "#!/bin/sh\nexec /usr/bin/python3 /usr/local/libexec/homelab_hostprep.py --wait-for-secrets\n"


def apply_local_host_config(root: Path = Path("/"), run: Callable[[list[str]], None] = _run) -> None:
    """Write the host files setup owns. Files that already match are not touched and trigger nothing."""
    docker_changed = merge_docker_config(root / "etc/docker/daemon.json")
    libexec = root / "usr/local/libexec"
    sysctl = root / "etc/sysctl.d/90-homelab-local.conf"
    units = [
        (
            root / "etc/systemd/system/homelab-infisical-agent.service",
            AGENT_UNIT,
            0o644,
        ),
        (root / "etc/systemd/system/homelab-secrets-ready.service", READY_UNIT, 0o644),
        (
            root / "etc/systemd/system/docker.service.d/10-homelab-local.conf",
            DOCKER_DROPIN,
            0o644,
        ),
    ]
    helpers = [
        (libexec / "homelab_hostprep.py", Path(__file__).read_bytes(), 0o644),
        (libexec / "homelab-wait-for-secrets", WAIT_HELPER.encode(), 0o755),
    ]
    units_changed = [replace_file(path, text.encode(), mode) for path, text, mode in units]
    for path, content, mode in helpers:
        replace_file(path, content, mode)
    if replace_file(sysctl, b"net.ipv4.ip_nonlocal_bind=1\n", 0o644):
        run(["sysctl", "--load", str(sysctl)])
    if any(units_changed):
        run(["systemctl", "daemon-reload"])
    if docker_changed:
        run(["systemctl", "try-restart", "docker"])  # log limits apply on restart; never starts a stopped Docker


def validate_target(
    os_release: Mapping[str, str],
    machine: str,
    memory_mib: int,
    public_ip: bool,
):
    if os_release.get("ID") != "ubuntu" or os_release.get("VERSION_ID") != "24.04" or machine != "x86_64":
        raise ValueError("only Ubuntu 24.04 amd64 is supported")
    if memory_mib < 4096:
        raise ValueError("at least 4096 MiB RAM is required")
    if public_ip:
        raise ValueError("a globally addressed interface was detected; setup refuses this host")


def _read_host_facts() -> tuple[dict[str, str], int, bool]:
    release = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            release[key] = value.strip('"')
    memory_kib = next(
        int(line.split()[1])
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
        if line.startswith("MemTotal:")
    )
    addresses = subprocess.run(["ip", "-j", "address"], capture_output=True, text=True, check=True)
    return release, memory_kib // 1024, _has_public_address(addresses.stdout)


def _has_public_address(ip_json: str) -> bool:
    """Whether `ip -j address` lists any globally routable address."""
    infos = (info for interface in json.loads(ip_json) for info in interface.get("addr_info", ()))
    return any(ipaddress.ip_address(info["local"]).is_global for info in infos)


def _listening_web_ports() -> set[str]:
    rows = subprocess.run(["ss", "-H", "-ltn"], capture_output=True, text=True, check=True).stdout.splitlines()
    return {row.split()[3].rsplit(":", 1)[-1] for row in rows if len(row.split()) > 3} & {"80", "443"}


def preflight(*, check_ports: bool = True) -> None:
    """Refuse an unsuitable host before anything is written. A host setup already serves skips the port check."""
    release, memory_mib, public_ip = _read_host_facts()
    validate_target(
        release,
        platform.machine(),
        memory_mib,
        public_ip,
    )
    if shutil.disk_usage("/").free < 2 * 1024**3:
        raise ValueError("less than 2 GiB is free on the root filesystem")
    if check_ports and (busy := sorted(_listening_web_ports())):
        raise ValueError(f"required web ports are already in use: {', '.join(busy)}")
    if os.geteuid() != 0:
        raise ValueError("run local host preparation as root")


def main() -> int:
    if sys.argv[1:] == ["--wait-for-secrets"]:
        try:
            timeout = os.environ.get("HOMELAB_SECRETS_TIMEOUT")  # for tests; unset means wait as long as it takes
            wait_for_secrets(
                Path("/etc/infisical-agent/config.yaml"), Path("/run/homelab-secrets"), timeout and float(timeout)
            )
        except (OSError, ValueError, TimeoutError) as error:
            print(f"homelab secret readiness failed: {error}", file=sys.stderr)
            return 1
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
