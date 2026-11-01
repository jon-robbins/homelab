"""Pinned bootstrap artifact retrieval. Never execute downloaded bytes before digest verification."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tarfile
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


def replace_file(destination: Path, content: bytes, mode: int) -> None:
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


def install_cosign(destination: Path) -> Path:
    pin = artifact_pin("cosign")
    return download_verified(pin["url"], pin["sha256"], destination)


def verify_signed_release(cosign: Path, artifact: Path, bundle: Path) -> None:
    subprocess.run(
        [
            str(cosign),
            "verify-blob",
            "--bundle",
            str(bundle),
            "--certificate-identity",
            "https://github.com/jon-robbins/homelab/.github/workflows/release.yml@refs/heads/main",
            "--certificate-oidc-issuer",
            "https://token.actions.githubusercontent.com",
            str(artifact),
        ],
        check=True,
    )


def merge_docker_config(path: Path) -> None:
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    config.setdefault("log-driver", "json-file")
    options = config.setdefault("log-opts", {})
    options.setdefault("max-size", "10m")
    options.setdefault("max-file", "3")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _download_docker_key(destination: Path) -> None:
    content = urlopen("https://download.docker.com/linux/ubuntu/gpg", timeout=30).read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as home:
        key_path = Path(home) / "docker.asc"
        key_path.write_bytes(content)
        result = subprocess.run(
            ["gpg", "--batch", "--show-keys", "--with-colons", str(key_path)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "GNUPGHOME": home},
        )
        fingerprints = [line.split(":")[9] for line in result.stdout.splitlines() if line.startswith("fpr:")]
        if artifact_pin("docker")["signing_key_fingerprint"] not in fingerprints:
            raise ValueError("Docker apt signing key fingerprint did not match the pin")
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)


def install_pinned_packages() -> None:
    # Establish the pinned bootstrap verifier before retrieving package metadata or payloads.
    install_cosign(Path("/usr/local/bin/cosign"))
    docker = artifact_pin("docker")
    ubuntu = artifact_pin("ubuntu_packages")
    _run(["apt-get", "update"])
    _run(["apt-get", "install", "-y", "ca-certificates", "curl", "gnupg"])
    key = Path("/etc/apt/keyrings/docker.asc")
    _download_docker_key(key)
    source = Path("/etc/apt/sources.list.d/docker.sources")
    source.write_text(
        "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\n"
        "Components: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\n",
        encoding="utf-8",
    )
    _run(["apt-get", "update"])
    _run(
        [
            "apt-get",
            "install",
            "-y",
            f"docker-ce={docker['engine']}",
            f"docker-ce-cli={docker['engine']}",
            f"containerd.io={docker['containerd']}",
            f"docker-buildx-plugin={docker['buildx']}",
            f"docker-compose-plugin={docker['compose']}",
            f"restic={ubuntu['restic']}",
            f"sqlite3={ubuntu['sqlite3']}",
            f"python3-venv={ubuntu['python3-venv']}",
        ]
    )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        infisical = artifact_pin("infisical")
        deb = download_verified(infisical["url"], infisical["sha256"], root / "infisical.deb")
        _run(["apt-get", "install", "-y", str(deb)])
        tailscale = artifact_pin("tailscale")
        archive = download_verified(tailscale["url"], tailscale["sha256"], root / "tailscale.tgz")
        with tarfile.open(archive, "r:gz") as package:
            prefix = f"tailscale_{tailscale['version']}_amd64/"
            files = []
            for member, target, mode in (
                (prefix + "tailscale", "/usr/sbin/tailscale", 0o755),
                (prefix + "tailscaled", "/usr/sbin/tailscaled", 0o755),
                (prefix + "systemd/tailscaled.service", "/etc/systemd/system/tailscaled.service", 0o644),
                (prefix + "systemd/tailscaled.defaults", "/etc/default/tailscaled", 0o644),
            ):
                source_file = package.extractfile(member)
                if source_file is None:
                    raise ValueError(f"Tailscale archive is missing {member}")
                files.append((Path(target), source_file.read(), mode))
        active = subprocess.run(["systemctl", "is-active", "--quiet", "tailscaled"]).returncode == 0
        if active:
            _run(["systemctl", "stop", "tailscaled"])
        for destination, content, mode in files:
            replace_file(destination, content, mode)
        _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", "--now", "tailscaled"])


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


def wait_for_secrets(config: Path, secret_dir: Path, timeout_s: int = 120) -> None:
    import re
    import time

    if not config.is_file():
        raise ValueError("Infisical agent config is missing")
    paths = sorted(set(re.findall(r"/run/homelab-secrets/[A-Z0-9_]+", config.read_text(encoding="utf-8"))))
    if not paths:
        raise ValueError("Infisical agent config has no rendered secret destinations")
    deadline = time.monotonic() + timeout_s
    while True:
        missing = [
            Path(path).name
            for path in paths
            if not (secret_dir / Path(path).name).is_file() or (secret_dir / Path(path).name).stat().st_size == 0
        ]
        if not missing:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"required secrets are not rendered: {', '.join(missing)}")
        time.sleep(1)


def apply_local_host_config() -> None:
    merge_docker_config(Path("/etc/docker/daemon.json"))
    sysctl = Path("/etc/sysctl.d/90-homelab-local.conf")
    sysctl.write_text("net.ipv4.ip_nonlocal_bind=1\n", encoding="utf-8")
    agent = Path("/etc/systemd/system/homelab-infisical-agent.service")
    agent.write_text(
        "[Unit]\nDescription=Homelab Infisical Agent (local setup)\nAfter=network-online.target\n"
        "Wants=network-online.target\n\n[Service]\nType=simple\nUser=homelab-secrets\nGroup=homelab\n"
        "RuntimeDirectory=homelab-secrets\nRuntimeDirectoryMode=0700\nRuntimeDirectoryPreserve=yes\n"
        "ExecStart=/usr/bin/infisical agent --config /etc/infisical-agent/config.yaml\n"
        "ExecStartPost=/usr/local/libexec/homelab-wait-for-secrets\nRestart=on-failure\nRestartSec=5\n\n"
        "[Install]\nWantedBy=multi-user.target\n",
        encoding="utf-8",
    )
    docker_dropin = Path("/etc/systemd/system/docker.service.d/10-homelab-local.conf")
    docker_dropin.parent.mkdir(parents=True, exist_ok=True)
    docker_dropin.write_text(
        "[Unit]\nRequires=tailscaled.service homelab-infisical-agent.service\n"
        "After=tailscaled.service homelab-infisical-agent.service\n",
        encoding="utf-8",
    )
    helper = Path("/usr/local/libexec/homelab-wait-for-secrets")
    helper.parent.mkdir(parents=True, exist_ok=True)
    library = helper.parent / "homelab_hostprep.py"
    library.write_bytes(Path(__file__).read_bytes())
    os.chmod(library, 0o644)
    helper.write_text("#!/bin/sh\nexec /usr/bin/python3 /usr/local/libexec/homelab_hostprep.py --wait-for-secrets\n")
    os.chmod(helper, 0o755)
    _run(["systemctl", "daemon-reload"])
    _run(["sysctl", "--load", str(sysctl)])


def validate_target(
    os_release: Mapping[str, str], machine: str, memory_mib: int, public_ip: bool, *, acknowledged=False
):
    if os_release.get("ID") != "ubuntu" or os_release.get("VERSION_ID") != "24.04" or machine != "x86_64":
        raise ValueError("only Ubuntu 24.04 amd64 is supported")
    if memory_mib < 4096:
        raise ValueError("at least 4096 MiB RAM is required")
    if public_ip and not acknowledged:
        raise ValueError("a public IP was detected; acknowledge this exposure before setup")


def preflight(*, acknowledge_public_ip: bool = False) -> None:
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
    public_ip = any(
        ipaddress.ip_address(info["local"]).is_global
        for interface in json.loads(addresses.stdout)
        for info in interface.get("addr_info", ())
    )
    validate_target(release, platform.machine(), memory_kib // 1024, public_ip, acknowledged=acknowledge_public_ip)
    disk = shutil.disk_usage("/")
    if disk.free < 2 * 1024**3:
        raise ValueError("less than 2 GiB is free on the root filesystem")
    listeners = subprocess.run(["ss", "-H", "-ltn"], capture_output=True, text=True, check=True).stdout
    busy = sorted({line.split()[-1].rsplit(":", 1)[-1] for line in listeners.splitlines()} & {"80", "443"})
    if busy:
        raise ValueError(f"required web ports are already in use: {', '.join(busy)}")
    if os.geteuid() != 0:
        raise ValueError("run local host preparation as root")


def main() -> int:
    if sys.argv[1:] == ["--wait-for-secrets"]:
        try:
            wait_for_secrets(
                Path("/etc/infisical-agent/config.yaml"),
                Path("/run/homelab-secrets"),
                int(os.environ.get("HOMELAB_SECRETS_TIMEOUT", "120")),
            )
        except (OSError, ValueError, TimeoutError) as error:
            print(f"homelab secret readiness failed: {error}", file=sys.stderr)
            return 1
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
