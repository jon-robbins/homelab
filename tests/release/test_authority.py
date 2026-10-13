from pathlib import Path

import pytest

from scripts.release.feed import FeedError, verify_cosign_bundle
from scripts.release.sign import record_bytes, signing_identity


def test_identity_is_exact(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/homelab")
    assert signing_identity() == "https://github.com/acme/homelab/.github/workflows/release.yml@refs/heads/main"


@pytest.mark.parametrize("failure", ["missing", "tampered", "identity"])
def test_cosign_verification_denies_invalid_bundle(tmp_path: Path, monkeypatch, failure):
    """The offline cosign double validates the actual record and identity."""
    record = {"schema": 1, "site_id": "home", "sequence": 1, "tooling_git_sha": "a" * 40,
              "sources": {"homelab": "a" * 40, "homelab-site": "a" * 40}, "images": {},
              "bundle_sha256": "sha256:" + "b" * 64, "rollback_floor": 0}
    record_path = tmp_path / "record.json"
    bundle_path = tmp_path / "record.sigstore.json"
    expected_record = record_bytes(record)
    record_path.write_bytes(expected_record)
    if failure != "missing":
        bundle_path.write_bytes(b"bundle")
    if failure == "tampered":
        record_path.write_bytes(expected_record.replace(b'"sequence":1', b'"sequence":2'))
    expected_identity = "https://github.com/jon-robbins/homelab/.github/workflows/release.yml@refs/heads/main"

    def fake_run(command, **kwargs):
        class Result:
            returncode = 0
            stderr = ""
            stdout = "verified"

        if (not bundle_path.is_file() or record_path.read_bytes() != expected_record or
                command[command.index("--certificate-identity") + 1] != expected_identity):
            Result.returncode = 1
            Result.stderr = "invalid"
        return Result()

    monkeypatch.setenv("GITHUB_REPOSITORY", "evil/other" if failure == "identity" else "jon-robbins/homelab")
    monkeypatch.setattr("scripts.release.feed.subprocess.run", fake_run)
    with pytest.raises(FeedError):
        verify_cosign_bundle(record_path, bundle_path)
