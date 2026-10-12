import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.release.bundle import bundle_sha256
from scripts.release.feed import FeedError, publish_feed, publish_next
from scripts.release.sign import DOMAIN, record_bytes, sign_record, verify_signature
from scripts.release.verify import VerificationError, VerificationResult, verify_release


SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64


def record(sequence=1):
    return {
        "schema": 1, "site_id": "home", "sequence": sequence, "tooling_git_sha": SHA,
        "sources": {"homelab": SHA, "homelab_site": SHA},
        "images": {"web": "ghcr.io/example/web@" + IMAGE_DIGEST},
        "bundle_sha256": bundle_sha256(b"bundle"), "rollback_floor": 0,
    }


class GitHubFake:
    def __init__(self, *, pull=None, runs=None):
        self.pull = pull
        self.runs = runs or []

    def commit(self, repository, sha):
        return {"parents": [{"sha": "1"}, {"sha": "2"}]}

    def pull_request(self, repository, sha):
        return self.pull

    def build_runs(self, repository, workflow, head_sha):
        return self.runs


_MISSING = object()


def valid_github(runs=None, pull=_MISSING):
    if pull is _MISSING:
        pull = {"merged": True, "base": "main", "merged_by": {"login": "owner"}}
    run = {"status": "completed", "conclusion": "success", "head_sha": SHA,
           "repository": "org/homelab", "workflow": "build.yml", "run_attempt": 1,
           "image": "web", "manifest_digest": IMAGE_DIGEST}
    return GitHubFake(pull=pull, runs=runs if runs is not None else [run])


def verify_args(client):
    return client, {"homelab": SHA, "homelab_site": SHA}, {"web": {
        "repository": "org/homelab", "workflow": "build.yml", "head_sha": SHA,
        "run_attempt": 1, "image_ref": "ghcr.io/example/web@" + IMAGE_DIGEST,
    }}


def test_authority_accepts_owner_merge_and_exact_run():
    result = verify_release(*verify_args(valid_github()), owners={"owner"}, required_images={"web"})
    assert isinstance(result, VerificationResult)
    assert result.sources == (SHA, SHA)


@pytest.mark.parametrize("pull, message", [
    (None, "owner-merged"),
    ({"merged": False, "base": "main", "merged_by": {"login": "owner"}}, "owner-merged"),
    ({"merged": True, "base": "develop", "merged_by": {"login": "owner"}}, "main"),
    ({"merged": True, "base": "main", "merged_by": {"login": "other"}}, "owner"),
])
def test_denies_unreviewed_wrong_base_or_non_owner(pull, message):
    with pytest.raises(VerificationError, match=message):
        verify_release(*verify_args(valid_github(pull=pull)), owners={"owner"}, required_images={"web"})


def test_denies_direct_head():
    fake = valid_github()
    fake.commit = lambda repository, sha: {"parents": [{"sha": "one"}]}
    with pytest.raises(VerificationError, match="merge commit"):
        verify_release(*verify_args(fake), owners={"owner"}, required_images={"web"})


def test_bootstrap_head_is_allowed():
    result = verify_release(*verify_args(valid_github()), bootstrap_shas={"homelab": [SHA], "homelab_site": [SHA]}, required_images={"web"})
    assert isinstance(result, VerificationResult)


@pytest.mark.parametrize("runs", [
    [{"status": "queued", "head_sha": SHA}],
    [{"status": "completed", "conclusion": "failure", "head_sha": SHA}],
    [{"status": "completed", "conclusion": "success", "head_sha": "d" * 40}],
])
def test_build_pending_or_denied(runs):
    args = verify_args(valid_github(runs=runs))
    if runs[0]["status"] != "completed":
        assert verify_release(*args, required_images={"web"}) == "pending"
    else:
        with pytest.raises(VerificationError):
            verify_release(*args, required_images={"web"})


def test_missing_image_and_wrong_attempt_denied():
    with pytest.raises(VerificationError, match="missing image"):
        verify_release(*verify_args(valid_github()), required_images={"web", "other"})
    with pytest.raises(VerificationError):
        verify_release(*verify_args(valid_github()), required_images={"web"}, expected_attempts={"web": 2})


def test_denies_successful_run_for_wrong_image():
    run = valid_github().runs[0].copy()
    run["image"] = "other"
    with pytest.raises(VerificationError, match="no exact successful build"):
        verify_release(*verify_args(valid_github(runs=[run])), required_images={"web"})


def test_denies_successful_run_with_mismatched_manifest_digest():
    run = valid_github().runs[0].copy()
    run["manifest_digest"] = "sha256:" + "c" * 64
    with pytest.raises(VerificationError, match="no exact successful build"):
        verify_release(*verify_args(valid_github(runs=[run])), required_images={"web"})


@pytest.mark.parametrize("field, value", [("caller", "untrusted"), ("id", 99), ("workflow", "other.yml"), ("head_sha", "d" * 40)])
def test_denies_wrong_run_authority(field, value):
    run = {"status": "completed", "conclusion": "success", "head_sha": SHA,
           "repository": "org/homelab", "workflow": "build.yml", "run_attempt": 1,
           "caller": "release-bot", "id": 7}
    run[field] = value
    args = list(verify_args(valid_github(runs=[run])))
    args[2]["web"].update(caller="release-bot", run_id=7)
    with pytest.raises(VerificationError):
        verify_release(*args, required_images={"web"})


def test_signature_is_domain_bound_and_tamper_evident(monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setenv("HOMELAB_RELEASE_SIGNING_KEY", base64.b64encode(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode())
    data, signature = sign_record(record())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert data == record_bytes(record())
    assert verify_signature(data, signature, public)
    assert not verify_signature(data + b"x", signature, public)


class FeedFake:
    def __init__(self, latest=None, move=False):
        self.latest, self.ref_value, self.move, self.commits = latest, "ref-1", move, []
        self.reads = 0

    def ref(self, repo, branch):
        return self.ref_value

    def read(self, repo, branch, path, allow_missing=False):
        if path.endswith("record.json") and self.latest is not None:
            value = record(self.latest) if isinstance(self.latest, int) else self.latest
            return record_bytes(value) if isinstance(value, dict) else value
        return None

    def last_record(self, repo, branch):
        return record(self.latest) if isinstance(self.latest, int) else self.latest

    def commit(self, repo, branch, ref, files, message):
        if self.move and not self.commits:
            self.ref_value = "ref-2"
            self.move = False
            raise RuntimeError("CAS changed")
        self.commits.append((ref, files, message))


def test_feed_noop_and_cas_retry_never_force_pushes():
    data = record_bytes(record(2))
    fake = FeedFake()
    assert publish_feed(fake, record(2), b"sig", b"bundle") == "published"
    assert fake.commits[0][0] == "ref-1"
    fake = FeedFake(move=True)
    assert publish_feed(fake, record(2), b"sig", b"bundle") == "published"
    assert fake.commits[0][0] == "ref-2"
    fake = FeedFake(latest=2)
    assert publish_feed(fake, record(2), b"sig", b"bundle") == "noop"


def test_feed_never_rewrites_sequence():
    with pytest.raises(FeedError, match="different"):
        publish_feed(FeedFake(latest=json.dumps(record(2)).encode()), record(2), b"sig", b"bundle")


def test_feed_rejects_signer_bytes_different_from_canonical_record():
    key = Ed25519PrivateKey.generate()

    def signer(next_record):
        altered = dict(next_record)
        altered["site_id"] = "other"
        data = record_bytes(altered)
        return data, key.sign(DOMAIN + data)

    with pytest.raises(FeedError, match="canonical record"):
        publish_next(FeedFake(), record(), signer, b"bundle")


def test_feed_rejects_mismatched_bundle_digest():
    with pytest.raises(FeedError, match="bundle_sha256"):
        publish_feed(FeedFake(), record(2), b"sig", b"different bundle")
