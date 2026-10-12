from pathlib import Path

import pytest

from scripts.release.feed import FeedError, verify_cosign_bundle
from scripts.release.sign import record_bytes, signing_identity


def test_identity_is_exact(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/homelab")
    assert signing_identity() == "https://github.com/acme/homelab/.github/workflows/release.yml@refs/heads/main"


@pytest.mark.parametrize("failure", ["missing", "tampered", "identity"])
def test_cosign_verification_denies_invalid_bundle(tmp_path: Path, monkeypatch, failure):
    record = {"schema": 1, "site_id": "home", "sequence": 1, "tooling_git_sha": "a" * 40,
              "sources": {"homelab": "a" * 40, "homelab-site": "a" * 40}, "images": {},
              "bundle_sha256": "sha256:" + "b" * 64, "rollback_floor": 0}
    record_path = tmp_path / "record.json"
    bundle_path = tmp_path / "record.sigstore.json"
    record_path.write_bytes(record_bytes(record))
    if failure != "missing":
        bundle_path.write_bytes(b"bundle")

    def fake_run(command, **kwargs):
        class Result:
            returncode = 1
            stderr = "invalid"
            stdout = ""
        if failure == "identity":
            assert "https://github.com/jon-robbins/homelab/.github/workflows/release.yml@refs/heads/main" in command
        return Result()

    monkeypatch.setattr("scripts.release.feed.subprocess.run", fake_run)
    with pytest.raises(FeedError):
        verify_cosign_bundle(record_path, bundle_path)
