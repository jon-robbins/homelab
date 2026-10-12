import json
from pathlib import Path

import pytest

from scripts.release.reconcile import check_feed, check_read, check_sign


ROOT = Path(__file__).parents[2]
TRUST = ROOT / "tests/release/fixtures/site/trust/release-keys.json"
TRUST_BYTES = TRUST.read_bytes()


class FakeHTTPGitHub:
    """Offline REST-shaped client for the credential checks."""

    def __init__(self, *, fail: str | None = None):
        self.fail = fail

    def ref(self, repository, branch):
        if self.fail == f"ref:{repository}:{branch}":
            raise RuntimeError("403")
        return f"{repository.replace('/', '-')}-{branch}-sha"

    def read(self, repository, branch, path):
        if self.fail == "trust":
            raise RuntimeError("403")
        return TRUST_BYTES

    def artifacts(self, repository):
        if self.fail == "build":
            return {"artifacts": []}
        return {"artifacts": [{"name": "oci-api"}, {"name": "provenance"}]}

    def repository(self, repository):
        if self.fail == "repository":
            raise RuntimeError("403")
        return {"permissions": {"push": True}}


def test_read_check_passes_and_hands_off_only_public_trust_map(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/homelab")
    results = check_read(FakeHTTPGitHub(), output=tmp_path / "trust.json")
    assert all(results)
    assert json.loads((tmp_path / "trust.json").read_text()) == json.loads(TRUST.read_text())
    assert "PRIVATE_INPUT_READ_TOKEN" not in capsys.readouterr().out


@pytest.mark.parametrize("fail", ["trust", "build", "ref:jon-robbins/homelab-site:main"])
def test_read_check_reports_failures(tmp_path, fail, capsys):
    assert not all(check_read(FakeHTTPGitHub(fail=fail), output=tmp_path / "trust.json"))
    assert "FAIL read" in capsys.readouterr().out


def test_sign_check_passes_and_rejects_mismatched_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RELEASE_SIGNING_KEY", "5e5ed75b05d493993afdc99609762aeee5f588da63346882dc3141936f83c983")
    monkeypatch.setenv("RELEASE_SIGNING_KEY_ID", "release-v1")
    assert all(check_sign(trust_map=TRUST))
    monkeypatch.setenv("RELEASE_SIGNING_KEY", "00" * 32)
    assert not all(check_sign(trust_map=TRUST))
    assert "FAIL sign" in capsys.readouterr().out


def test_feed_check_passes_and_reports_denied_push():
    assert all(check_feed(FakeHTTPGitHub()))
    assert not all(check_feed(FakeHTTPGitHub(fail="repository")))


def test_checks_do_not_call_record_signing_or_feed_publish(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("check reached release mutation")

    monkeypatch.setattr("scripts.release.reconcile.sign_record", forbidden)
    monkeypatch.setattr("scripts.release.reconcile.feed_plan", forbidden)
    monkeypatch.setenv("RELEASE_SIGNING_KEY", "5e5ed75b05d493993afdc99609762aeee5f588da63346882dc3141936f83c983")
    monkeypatch.setenv("RELEASE_SIGNING_KEY_ID", "release-v1")
    assert all(check_read(FakeHTTPGitHub(), output=tmp_path / "trust.json"))
    assert all(check_sign(trust_map=TRUST))
    assert all(check_feed(FakeHTTPGitHub()))
