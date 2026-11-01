import hashlib
import json

import pytest

from setup.hostprep import (
    artifact_pin,
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


def test_cosign_bootstrap_pin_is_versioned_and_sha256_pinned():
    pin = artifact_pin("cosign")

    assert pin["version"] == "3.1.3"
    assert len(pin["sha256"]) == 64
    assert pin["url"].endswith("/v3.1.3/cosign-linux-amd64")


def test_install_replaces_existing_binary_atomically_and_sets_its_mode(tmp_path):
    destination = tmp_path / "tailscaled"
    destination.write_bytes(b"old running executable")

    replace_file(destination, b"new executable", 0o755)

    assert destination.read_bytes() == b"new executable"
    assert destination.stat().st_mode & 0o777 == 0o755
    assert sorted(path.name for path in tmp_path.iterdir()) == ["tailscaled"]


def test_preflight_accepts_only_ubuntu_2404_amd64_with_public_ip_acknowledged():
    validate_target({"ID": "ubuntu", "VERSION_ID": "24.04"}, "x86_64", 8192, False)
    with pytest.raises(ValueError, match="public IP"):
        validate_target({"ID": "ubuntu", "VERSION_ID": "24.04"}, "x86_64", 8192, True)
    validate_target({"ID": "ubuntu", "VERSION_ID": "24.04"}, "x86_64", 8192, True, acknowledged=True)
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
