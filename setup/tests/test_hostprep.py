import hashlib
import json

import pytest

from setup import hostprep
from setup.hostprep import (
    apply_local_host_config,
    install_cosign,
    merge_docker_config,
    replace_file,
    validate_target,
    verify_digest,
    wait_for_secrets,
)


def test_verified_download_rejects_bytes_that_do_not_match_the_pinned_digest():
    expected = hashlib.sha256(b"pinned content").hexdigest()

    assert verify_digest(b"pinned content", expected) == b"pinned content"
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_digest(b"tampered content", expected)


def test_install_replaces_existing_binary_atomically_and_sets_its_mode(tmp_path):
    destination = tmp_path / "executable"
    destination.write_bytes(b"old running executable")

    replace_file(destination, b"new executable", 0o755)

    assert destination.read_bytes() == b"new executable"
    assert destination.stat().st_mode & 0o777 == 0o755
    assert sorted(path.name for path in tmp_path.iterdir()) == ["executable"]


def test_preflight_accepts_only_ubuntu_2404_amd64_and_refuses_public_ipv4():
    validate_target({"ID": "ubuntu", "VERSION_ID": "24.04"}, "x86_64", 8192, False)
    with pytest.raises(ValueError, match="public IPv4"):
        validate_target({"ID": "ubuntu", "VERSION_ID": "24.04"}, "x86_64", 8192, True)
    with pytest.raises(ValueError, match="Ubuntu 24.04 amd64"):
        validate_target({"ID": "debian", "VERSION_ID": "12"}, "x86_64", 8192, False)


def test_docker_logging_defaults_merge_without_replacing_existing_config(tmp_path):
    path = tmp_path / "daemon.json"
    path.write_text(json.dumps({"data-root": str(tmp_path / "docker"), "log-opts": {"max-file": "5"}}))

    merge_docker_config(path)

    result = json.loads(path.read_text())
    assert result["data-root"] == str(tmp_path / "docker")
    assert result["log-opts"] == {"max-file": "5", "max-size": "10m"}
    assert result["log-driver"] == "json-file"


def test_local_secret_readiness_fails_when_a_required_secret_is_missing(tmp_path):
    config = tmp_path / "agent.yaml"
    config.write_text("destination-path: /run/homelab-secrets/REQUIRED_KEY\n")

    with pytest.raises(TimeoutError, match="REQUIRED_KEY"):
        wait_for_secrets(config, tmp_path / "secrets", timeout_s=0)


def test_replace_file_leaves_an_identical_file_untouched(tmp_path):
    target = tmp_path / "unit.service"
    assert replace_file(target, b"same", 0o644) is True
    before = target.stat().st_mtime_ns

    assert replace_file(target, b"same", 0o644) is False
    assert target.stat().st_mtime_ns == before
    assert replace_file(target, b"different", 0o644) is True


def test_installed_cosign_matching_the_pin_is_not_downloaded_again(tmp_path, monkeypatch):
    content = b"pinned cosign"
    monkeypatch.setattr(
        hostprep,
        "artifact_pin",
        lambda name: {"url": "unused", "sha256": hashlib.sha256(content).hexdigest()},
    )
    target = tmp_path / "cosign"
    replace_file(target, content, 0o755)

    def refuse(*args, **kwargs):
        raise AssertionError("downloaded an artifact that was already installed")

    monkeypatch.setattr(hostprep, "urlopen", refuse)
    assert install_cosign(target) == target


class Commands:
    def __init__(self):
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)


def test_host_config_applies_once_and_a_rerun_runs_nothing(tmp_path):
    first, second = Commands(), Commands()

    apply_local_host_config(tmp_path, first)
    apply_local_host_config(tmp_path, second)

    assert ["systemctl", "daemon-reload"] in first.calls
    assert ["systemctl", "try-restart", "docker"] in first.calls
    assert second.calls == []


def test_log_limits_are_not_added_to_a_driver_that_rejects_them(tmp_path):
    path = tmp_path / "daemon.json"
    path.write_text(json.dumps({"log-driver": "journald"}))

    assert merge_docker_config(path) is False
    assert json.loads(path.read_text()) == {"log-driver": "journald"}


def _addresses(*locals_):
    return json.dumps([{"ifname": "eth0", "addr_info": [{"local": address} for address in locals_]}])


@pytest.mark.parametrize(
    ("addresses", "public"),
    [
        (("127.0.0.1", "169.254.1.1", "fe80::1", "198.51.100.7"), set()),
        (("8.8.8.8",), {4}),
        (("2606:4700:4700::1111", "fd00::1"), {6}),
    ],
)
def test_public_address_detection_separates_ipv4_from_ipv6(addresses, public):
    assert hostprep._public_families(_addresses(*addresses)) == public


def test_merging_docker_defaults_reports_whether_it_changed_anything(tmp_path):
    path = tmp_path / "daemon.json"

    assert merge_docker_config(path) is True
    assert merge_docker_config(path) is False


def test_port_check_can_be_skipped_for_a_host_that_setup_already_serves(monkeypatch):
    seen = []
    monkeypatch.setattr(hostprep, "validate_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(hostprep.shutil, "disk_usage", lambda path: type("U", (), {"free": 10**12})())
    monkeypatch.setattr(hostprep.os, "geteuid", lambda: 0)
    monkeypatch.setattr(hostprep, "_listening_web_ports", lambda: seen.append("probed") or {"443"})
    monkeypatch.setattr(hostprep, "_read_host_facts", lambda: ({}, 8192, set()))

    hostprep.preflight(check_ports=False)
    assert seen == []
    with pytest.raises(ValueError, match="443"):
        hostprep.preflight()


def test_listening_port_parser_uses_local_address_and_ignores_setup_caddy_peer(monkeypatch):
    output = """LISTEN 0 4096 0.0.0.0:80 0.0.0.0:*
LISTEN 0 4096 0.0.0.0:443 8.8.8.8:52344
LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*
"""
    monkeypatch.setattr(hostprep.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": output})())

    assert hostprep._listening_web_ports() == {"80", "443"}
