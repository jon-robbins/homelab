import base64
import io
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.release.bundle import bundle_sha256
from scripts.release.feed import FeedError, publish_feed, publish_next
from scripts.release.github import GitHubClient
from scripts.release.sign import DOMAIN, record_bytes, sign_record, verify_signature
from scripts.release.reconcile import feed_plan, plan as reconcile_plan, sign_plan
from scripts.release.verify import VerificationError, VerificationResult, verify_release


SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
FEED_KEY = Ed25519PrivateKey.generate()
FEED_PUBLIC_KEY = FEED_KEY.public_key()


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

    def read_commit(self, repository, sha):
        return {"parents": [{"sha": "1"}, {"sha": "2"}]}

    def pull_request(self, repository, sha):
        return self.pull

    def build_runs(self, repository, workflow, head_sha):
        return self.runs


class PullRequestListFake(GitHubFake):
    def pull_requests_for_commit(self, repository, sha):
        return [self.pull] if self.pull is not None else []


_MISSING = object()


def valid_github(runs=None, pull=_MISSING):
    if pull is _MISSING:
        pull = {"merged": True, "base": "main", "merge_commit_sha": SHA,
                "merged_by": {"login": "owner"}}
    run = {"status": "completed", "conclusion": "success", "head_sha": SHA,
           "repository": "homelab", "workflow": "build.yml", "run_attempt": 1,
           "image": "web", "manifest_digest": IMAGE_DIGEST}
    return GitHubFake(pull=pull, runs=runs if runs is not None else [run])


def verify_args(client):
    return client, {"homelab": SHA, "homelab_site": SHA}, {"web": {
        "repository": "homelab", "workflow": "build.yml", "head_sha": SHA,
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


@pytest.mark.parametrize("owners", [(), set()])
def test_denies_non_owner_without_configured_owner_policy(owners):
    pull = {"merged": True, "base": "main", "merged_by": {"login": "other"}}
    with pytest.raises(VerificationError, match="owner"):
        verify_release(*verify_args(valid_github(pull=pull)), owners=owners, required_images={"web"})


def test_denies_direct_head():
    fake = valid_github()
    fake.read_commit = lambda repository, sha: {"parents": [{"sha": "one"}]}
    with pytest.raises(VerificationError, match="merge commit"):
        verify_release(*verify_args(fake), owners={"owner"}, required_images={"web"})


@pytest.mark.parametrize("commit, message", [
    (None, "unknown head"),
    ({"parents": [{"sha": "one"}]}, "merge commit"),
])
def test_pull_request_list_path_still_checks_head(commit, message):
    fake = PullRequestListFake(**valid_github().__dict__)
    fake.read_commit = lambda repository, sha: commit
    with pytest.raises(VerificationError, match=message):
        verify_release(*verify_args(fake), owners={"owner"}, required_images={"web"})


def test_denies_pr_with_different_merge_commit():
    pull = {"merged": True, "base": "main", "merge_commit_sha": "c" * 40,
            "merged_by": {"login": "owner"}}
    with pytest.raises(VerificationError, match="merge commit"):
        verify_release(*verify_args(valid_github(pull=pull)), owners={"owner"}, required_images={"web"})


def test_denies_unknown_head_and_missing_source_head():
    fake = valid_github()
    fake.read_commit = lambda repository, sha: None
    with pytest.raises(VerificationError, match="unknown head"):
        verify_release(*verify_args(fake), owners={"owner"}, required_images={"web"})
    with pytest.raises(VerificationError, match="missing source head"):
        verify_release(valid_github(), {"homelab": SHA}, verify_args(valid_github())[2],
                       owners={"owner"}, required_images={"web"})


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
        assert verify_release(*args, owners={"owner"}, required_images={"web"}) == "pending"
    else:
        with pytest.raises(VerificationError):
            verify_release(*args, owners={"owner"}, required_images={"web"})


def test_missing_image_and_wrong_attempt_denied():
    with pytest.raises(VerificationError, match="missing image"):
        verify_release(*verify_args(valid_github()), owners={"owner"}, required_images={"web", "other"})
    with pytest.raises(VerificationError):
        verify_release(*verify_args(valid_github()), owners={"owner"}, required_images={"web"}, expected_attempts={"web": 2})


def test_denies_successful_run_for_wrong_image():
    run = valid_github().runs[0].copy()
    run["image"] = "other"
    with pytest.raises(VerificationError, match="no exact successful build"):
        verify_release(*verify_args(valid_github(runs=[run])), owners={"owner"}, required_images={"web"})


def test_denies_successful_run_with_mismatched_manifest_digest():
    run = valid_github().runs[0].copy()
    run["manifest_digest"] = "sha256:" + "c" * 64
    with pytest.raises(VerificationError, match="no exact successful build"):
        verify_release(*verify_args(valid_github(runs=[run])), owners={"owner"}, required_images={"web"})


def test_denies_successful_run_at_unverified_build_head():
    unverified = "d" * 40
    run = valid_github().runs[0].copy()
    run["head_sha"] = unverified
    args = list(verify_args(valid_github(runs=[run])))
    args[2]["web"]["head_sha"] = unverified
    with pytest.raises(VerificationError, match="verified source head"):
        verify_release(*args, owners={"owner"}, required_images={"web"})


def test_denies_missing_manifest_digest_and_unverified_image_repository():
    args = list(verify_args(valid_github()))
    args[2]["web"].pop("image_ref")
    args[2]["web"].pop("manifest_digest", None)
    with pytest.raises(VerificationError, match="manifest digest"):
        verify_release(*args, owners={"owner"}, required_images={"web"})
    args = verify_args(valid_github())
    args[2]["web"]["repository"] = "org/other"
    with pytest.raises(VerificationError, match="no verified source head"):
        verify_release(*args, owners={"owner"}, required_images={"web"},
                       repositories={"homelab": "org/homelab", "homelab-site": "org/site"})


def test_denies_unmapped_image_repository_without_repositories():
    args = list(verify_args(valid_github()))
    args[2]["web"]["repository"] = "org/other"
    args[0].runs[0]["repository"] = "org/other"
    with pytest.raises(VerificationError, match="no verified source head"):
        verify_release(*args, owners={"owner"}, required_images={"web"})


@pytest.mark.parametrize("field, value", [("caller", "untrusted"), ("id", 99), ("workflow", "other.yml"), ("head_sha", "d" * 40)])
def test_denies_wrong_run_authority(field, value):
    run = {"status": "completed", "conclusion": "success", "head_sha": SHA,
           "repository": "org/homelab", "workflow": "build.yml", "run_attempt": 1,
           "caller": "release-bot", "id": 7}
    run[field] = value
    args = list(verify_args(valid_github(runs=[run])))
    args[2]["web"].update(caller="release-bot", run_id=7)
    with pytest.raises(VerificationError):
        verify_release(*args, owners={"owner"}, required_images={"web"})


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

    def commit_files(self, repo, branch, ref, files, message):
        if self.move and not self.commits:
            self.ref_value = "ref-2"
            self.move = False
            raise RuntimeError("CAS changed")
        self.commits.append((ref, files, message))


def test_feed_noop_and_cas_retry_never_force_pushes():
    data = record_bytes(record(2))
    signature = FEED_KEY.sign(DOMAIN + data)
    fake = FeedFake()
    assert publish_feed(fake, record(2), signature, b"bundle", public_key=FEED_PUBLIC_KEY) == "published"
    assert fake.commits[0][0] == "ref-1"
    fake = FeedFake(move=True)
    assert publish_feed(fake, record(2), signature, b"bundle", public_key=FEED_PUBLIC_KEY) == "published"
    assert fake.commits[0][0] == "ref-2"
    fake = FeedFake(latest=2)
    assert publish_feed(fake, record(2), signature, b"bundle", public_key=FEED_PUBLIC_KEY) == "noop"


def test_feed_never_rewrites_sequence():
    with pytest.raises(FeedError, match="different"):
        data = record_bytes(record(2))
        publish_feed(FeedFake(latest=json.dumps(record(2)).encode()), record(2),
                     FEED_KEY.sign(DOMAIN + data), b"bundle", public_key=FEED_PUBLIC_KEY)


def test_feed_rejects_signer_bytes_different_from_canonical_record():
    key = Ed25519PrivateKey.generate()

    def signer(next_record):
        altered = dict(next_record)
        altered["site_id"] = "other"
        data = record_bytes(altered)
        return data, key.sign(DOMAIN + data)

    with pytest.raises(FeedError, match="canonical record"):
        publish_next(FeedFake(), record(), signer, b"bundle", public_key=key.public_key())


def test_feed_rejects_mismatched_bundle_digest():
    with pytest.raises(FeedError, match="bundle_sha256"):
        publish_feed(FeedFake(), record(2), b"invalid", b"different bundle", public_key=FEED_PUBLIC_KEY)


def test_feed_rejects_invalid_signature():
    data = record_bytes(record(2))
    with pytest.raises(FeedError, match="signature"):
        publish_feed(FeedFake(), record(2), b"invalid", b"bundle", public_key=FEED_PUBLIC_KEY)


def test_feed_rejects_placeholder_public_key(tmp_path):
    (tmp_path / "release-signing.pub").write_text("PLACEHOLDER", encoding="utf-8")
    with pytest.raises(ValueError, match="placeholder"):
        feed_plan(tmp_path, tmp_path / "release-signing.pub")


class RestReleaseFake:
    """Small REST-shaped fake: workflow runs use repository objects like GitHub does."""

    def __init__(self, images=("homelab-agent", "caddy"), *, run_sha=SHA):
        self.images = images
        self.run_sha = run_sha
        self.commits = []
        self.site_root = Path(__file__).parent / "fixtures" / "site"

    def ref(self, repository, branch):
        return SHA if repository == "jon-robbins/homelab" else "f" * 40

    def last_record(self, repository, branch):
        return None

    def workflow_runs(self, repository, workflow, head_sha):
        return {"workflow_runs": [
            {"id": 42, "run_attempt": 1, "created_at": "2026-10-12T00:00:00Z",
             "status": "completed", "conclusion": "success", "head_sha": self.run_sha,
             "repository": {"full_name": repository},
             "path": workflow + "@refs/heads/main"}
        ]}

    def run_artifacts(self, repository, run_id):
        return {"artifacts": [{"id": index + 1, "name": f"oci-{image}"}
                               for index, image in enumerate(self.images)]}

    def artifact_files(self, repository, artifact):
        image = artifact["name"][4:]
        digest = "sha256:" + ("1" if image == "homelab-agent" else "2") * 64
        return {"provenance.json": json.dumps({
            "schema": 1,
            "repository": repository,
            "workflow_ref": f"{repository}/.github/workflows/post-merge-build.yml@refs/heads/main",
            "run_id": 42,
            "run_attempt": 1,
            "source_sha": self.run_sha,
            "image": image,
            "manifest_digest": digest,
            "built_at": "2026-10-12T00:00:00Z",
        }).encode()}

    def read_commit(self, repository, value):
        return {"parents": [{"sha": "parent-1"}, {"sha": "parent-2"}]}

    def commit_files(self, repository, branch, ref, files, message):
        self.commits.append((repository, branch, ref, files, message))
        return "published-commit"

    def pull_requests_for_commit(self, repository, sha):
        return [{"merged": True, "base_ref": "main", "merge_commit_sha": sha,
                 "merged_by": {"login": "jon-robbins"}}]

    def read(self, repository, branch, path, allow_missing=False):
        value = self.site_root / path
        return value.read_bytes() if value.is_file() else None

    def contents(self, repository, ref, path=""):
        directory = self.site_root / path
        return [{"name": item.name, "type": "dir" if item.is_dir() else "file"}
                for item in sorted(directory.iterdir())] if directory.is_dir() else None

def test_reconcile_plan_sign_feed_offline_end_to_end(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    raw_key = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    monkeypatch.setenv("RELEASE_OWNERS", "jon-robbins")
    monkeypatch.setenv("RELEASE_SIGNING_KEY", raw_key.hex())
    fake = RestReleaseFake()

    planned = reconcile_plan(fake, repository="jon-robbins/homelab", output=tmp_path / "release-plan.json")
    assert [item["image"] for item in planned["images"]] == ["caddy", "homelab-agent"]
    output = tmp_path / "release-output"
    sign_plan(tmp_path / "release-plan.json", output)
    public_key = tmp_path / "release-signing.pub"
    public_key.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    assert (output / "record.json").is_file()
    assert (output / "record.sig").is_file()
    assert (output / "bundle.tar").is_file()
    assert verify_signature((output / "record.json").read_bytes(), (output / "record.sig").read_bytes(), public_key.read_bytes())
    assert feed_plan(output, public_key, client=fake) == "published"
    assert fake.commits


def test_reconcile_denies_unapproved_destination():
    with pytest.raises(VerificationError, match="unapproved"):
        reconcile_plan(RestReleaseFake(("not-approved",)), repository="jon-robbins/homelab")


def test_reconcile_denies_build_for_another_sha():
    with pytest.raises(VerificationError, match="no successful"):
        reconcile_plan(RestReleaseFake(run_sha="d" * 40), repository="jon-robbins/homelab")


def test_reconcile_denies_mismatched_artifact_provenance():
    class MismatchedProvenance(RestReleaseFake):
        def artifact_files(self, repository, artifact):
            files = super().artifact_files(repository, artifact)
            provenance = json.loads(files["provenance.json"])
            provenance["run_id"] = 99
            files["provenance.json"] = json.dumps(provenance).encode()
            return files

    with pytest.raises(VerificationError, match="provenance run_id"):
        reconcile_plan(MismatchedProvenance(), repository="jon-robbins/homelab")


def test_reconcile_feed_denies_bad_signature(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    raw_key = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    monkeypatch.setenv("RELEASE_SIGNING_KEY", raw_key.hex())
    fake = RestReleaseFake()
    planned = reconcile_plan(fake, repository="jon-robbins/homelab")
    for name, value in {
        "RELEASE_COMPOSE": "compose.json", "RELEASE_MANIFEST": "manifest.yaml",
        "RELEASE_PROBES": "probes.json", "RELEASE_POLICY": "policy.yaml", "RELEASE_FILES": "files",
    }.items():
        monkeypatch.setenv(name, str(Path(__file__).parent / "fixtures" / "site" / value))
    output = tmp_path / "release-output"
    plan_path = tmp_path / "release-plan.json"
    plan_path.write_text(json.dumps(planned), encoding="utf-8")
    sign_plan(plan_path, output)
    (output / "record.sig").write_bytes(b"bad signature")
    public_key = tmp_path / "release-signing.pub"
    public_key.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(FeedError, match="signature"):
        feed_plan(output, public_key, client=fake)


class _OfflineHTTPResponse:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class _OfflineGitHub:
    """HTTP transport for the reconciler's real GitHubClient."""

    def __init__(self, sha, site_root):
        self.sha = sha
        self.site_root = site_root
        self.release_ref = "release-ref"
        self.published = False

    def __call__(self, request):
        url = urlsplit(request.full_url)
        path = url.path
        query = parse_qs(url.query)
        method = request.method
        if path.endswith("/commits") and method == "GET" and "/actions/" not in path:
            return self._json([{"sha": self.sha}])
        if "/actions/workflows/" in path and path.endswith("/runs"):
            return self._json({"workflow_runs": [{
                "id": 42, "run_attempt": 1, "created_at": "2026-10-12T00:00:00Z",
                "status": "completed", "conclusion": "success", "head_sha": self.sha,
                "repository": {"full_name": "jon-robbins/homelab"},
                "path": ".github/workflows/post-merge-build.yml@refs/heads/main",
            }]})
        if "/actions/runs/42/artifacts" in path:
            return self._json({"artifacts": [
                {"id": 1, "name": "oci-caddy"},
                {"id": 2, "name": "oci-homelab-agent"},
            ]})
        if "/actions/artifacts/" in path and path.endswith("/zip"):
            artifact_id = path.split("/")[-2]
            image = "caddy" if artifact_id == "1" else "homelab-agent"
            digest = "sha256:" + ("1" if image == "caddy" else "2") * 64
            provenance = json.dumps({
                "schema": 1, "repository": "jon-robbins/homelab",
                "workflow_ref": "jon-robbins/homelab/.github/workflows/post-merge-build.yml@refs/heads/main",
                "run_id": 42, "run_attempt": 1, "source_sha": self.sha,
                "image": image, "manifest_digest": digest,
                "built_at": "2026-10-12T00:00:00Z",
            }).encode()
            archive = io.BytesIO()
            with ZipFile(archive, "w", ZIP_DEFLATED) as zipped:
                zipped.writestr("provenance.json", provenance)
            return _OfflineHTTPResponse(archive.getvalue(), {"Content-Type": "application/zip"})
        if "/git/ref/heads/" in path:
            if path.endswith("/releases"):
                return self._json({"object": {"sha": self.release_ref}})
            return self._json({"object": {"sha": self.sha if "jon-robbins/homelab/" in path else "f" * 40}})
        if path.endswith("/pulls"):
            merged_sha = path.split("/commits/", 1)[1].split("/pulls", 1)[0]
            return self._json([{"merged": True, "base_ref": "main", "merge_commit_sha": merged_sha,
                                "merged_by": {"login": "jon-robbins"}}])
        if "/commits/" in path and "/git/commits/" not in path:
            return self._json({"parents": [{"sha": "parent-1"}, {"sha": "parent-2"}]})
        if "/contents/records" in path:
            if query.get("ref") == ["releases"] and not path.endswith("/record.json"):
                if self.published:
                    return self._json([{"name": "1", "type": "dir"}])
                return self._json([], {})
            if path.endswith("/record.json") and self.published:
                return self._json({"content": base64.b64encode(self.record).decode()})
            return self._not_found(path)
        if "/contents" in path and method == "GET":
            content_path = path.split("/contents", 1)[1].lstrip("/")
            if content_path == "files":
                return self._json([{"name": "settings.ini", "type": "file"}])
            if content_path == "files/settings.ini":
                return self._file(self.site_root / "files/settings.ini")
            if content_path in {"compose.json", "manifest.yaml", "probes.json", "policy.yaml"}:
                return self._file(self.site_root / content_path)
            return self._not_found(path)
        if path.endswith("/git/commits/release-ref"):
            return self._json({"tree": {"sha": "tree-ref"}})
        if path.endswith("/git/blobs"):
            return self._json({"sha": "blob"})
        if path.endswith("/git/trees"):
            return self._json({"sha": "tree-new"})
        if path.endswith("/git/commits") and method == "POST":
            return self._json({"sha": "commit-new"})
        if "/git/refs/heads/releases" in path and method == "PATCH":
            self.published = True
            return self._json(None)
        raise AssertionError(f"unhandled GitHub request: {method} {path}")

    @staticmethod
    def _json(value, headers=None):
        body = json.dumps(value).encode()
        return _OfflineHTTPResponse(body, headers or {"Content-Type": "application/json"})

    @staticmethod
    def _file(path):
        return _OfflineGitHub._json({"content": base64.b64encode(path.read_bytes()).decode()})

    @staticmethod
    def _not_found(path):
        raise HTTPError(path, 404, "not found", {}, io.BytesIO(b"not found"))


def test_reconcile_plan_sign_feed_uses_real_github_client(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    raw_key = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    monkeypatch.setenv("RELEASE_OWNERS", "jon-robbins")
    monkeypatch.setenv("RELEASE_SIGNING_KEY", raw_key.hex())
    transport = _OfflineGitHub(SHA, Path(__file__).parent / "fixtures" / "site")
    monkeypatch.setattr("scripts.release.github.urlopen", transport)
    client = GitHubClient("offline-token")

    planned = reconcile_plan(client, repository="jon-robbins/homelab", output=tmp_path / "release-plan.json")
    assert {item["image"] for item in planned["images"]} == {"caddy", "homelab-agent"}
    output = tmp_path / "release-output"
    sign_plan(tmp_path / "release-plan.json", output)
    public_key = tmp_path / "release-signing.pub"
    public_key.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    assert verify_signature((output / "record.json").read_bytes(), (output / "record.sig").read_bytes(), public_key.read_bytes())
    assert feed_plan(output, public_key, client=client) == "published"
