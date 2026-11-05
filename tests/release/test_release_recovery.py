"""Recover a stacked merge's missing producer without weakening input verification."""
import pytest

from scripts.release import compile as release_compile, reconcile
from scripts.release.verify import VerificationError, VerificationResult

IMAGES = {"seerr-oidc", "agent"}


def run(sha, *, status="completed", conclusion="success"):
    return {"id": int(sha[1:]) + 1, "head_sha": sha, "event": "push", "head_branch": "main",
            "path": reconcile.BUILD_WORKFLOW, "status": status, "conclusion": conclusion}


class History:
    def __init__(self):
        self.runs = {"h2": [run("h2")]}
        self.artifact_list = [
            {"name": "oci-" + image, "workflow_run": {"id": 3, "head_sha": "h2"}}
            for image in IMAGES
        ]
        self.comparison = "ahead"

    def read_commit(self, _repository, sha):
        index = int(sha[1:])
        return {"parents": [{"sha": f"h{index - 1}"}] if index else [],
                "files": [{"filename": "config/seerr-oidc/build.py"}]}

    def compare_commits(self, *_args):
        return {"status": self.comparison}

    def workflow_runs(self, _repository, _workflow, sha):
        return {"workflow_runs": self.runs.get(sha, [])}

    def artifacts(self, _repository):
        return {"artifacts": self.artifact_list}

    def last_record(self, *_args):
        return {"sequence": 5, "sources": {"homelab": "h0", "homelab-site": "site0"}, "images": {}}

    def ref(self, *_args):
        return "site1"

    def read(self, *_args):
        return b"site_id: synthetic\nplugins: {}\n"


@pytest.fixture
def planned(monkeypatch, tmp_path):
    client = History()
    calls = {"attested": [], "rendered": [], "verified": []}
    attested = reconcile._attested_builds

    def builds(repository_client, repository, sha):
        calls["attested"].append(sha)
        if sha != "h2":
            return attested(repository_client, repository, sha)
        return {image: {"head_sha": sha, "artifact_name": "oci-" + image, "run_id": 3,
                        "manifest_digest": "sha256:" + "a" * 64} for image in IMAGES}

    def render(_client, _platform, _site, site_sha, platform_sha, _output, **_kwargs):
        calls["rendered"].append(platform_sha)
        return {"homelab": platform_sha, "homelab-site": site_sha}, object()

    def verify(_client, sources, selected, **kwargs):
        calls["verified"].append((sources, selected, kwargs))
        return VerificationResult(tuple(sources.values()), {image: "sha256:" + "a" * 64 for image in selected}, {})

    monkeypatch.setenv("GITHUB_SHA", "h3")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("RELEASE_INPUTS_DIR", str(tmp_path))
    monkeypatch.setattr(reconcile, "_check_homelab_ruleset", lambda *_: None)
    monkeypatch.setattr(reconcile, "_check_homelab_head_ancestry", lambda *_: None)
    monkeypatch.setattr(reconcile, "load_approved_destinations", lambda: {"ghcr.io/jon-robbins/homelab/" + image for image in IMAGES})
    monkeypatch.setattr(reconcile, "_attested_builds", builds)
    monkeypatch.setattr(reconcile, "render_release", render)
    monkeypatch.setattr(reconcile, "verify_release", verify)
    monkeypatch.setattr(release_compile, "compile_bundle", lambda *_: None)
    return client, calls


def test_missing_stacked_build_releases_later_full_build_and_renders_that_source(planned):
    client, calls = planned
    result = reconcile.plan(client, repository="platform", site_repository="site")
    assert result["sha"] == result["sources"]["homelab"] == "h2"
    assert result["sequence"] == 6 and result["release"]
    assert calls["attested"] == ["h1", "h2"]
    assert calls["rendered"] == ["h2"]
    sources, selected, policy = calls["verified"][0]
    assert sources["homelab"] == "h2" and set(selected) == IMAGES
    assert policy["required_images"] == IMAGES
    assert policy["ruleset_repositories"] == {"homelab"}


@pytest.mark.parametrize("status,conclusion", [("in_progress", None), ("completed", "failure")])
def test_existing_running_or_failed_build_does_not_take_recovery_path(planned, status, conclusion):
    client, calls = planned
    client.runs["h1"] = [run("h1", status=status, conclusion=conclusion)]
    with pytest.raises(VerificationError, match="still running or failed"):
        reconcile.plan(client, repository="platform", site_repository="site")
    assert calls["attested"] == ["h1"]
    assert calls["rendered"] == []


def test_recovery_cannot_reuse_full_build_older_than_blocked_commit(planned):
    client, calls = planned
    client.artifact_list = [{"name": "oci-" + image, "workflow_run": {"id": 1, "head_sha": "h0"}} for image in IMAGES]
    client.runs["h0"] = [run("h0")]
    with pytest.raises(VerificationError):
        reconcile.plan(client, repository="platform", site_repository="site")
    assert calls["attested"] == ["h1"]
    assert calls["rendered"] == []


@pytest.mark.parametrize("defect", ["partial", "expired", "other-run"])
def test_recovery_requires_a_complete_current_artifact_set(planned, defect):
    client, calls = planned
    if defect == "partial":
        client.artifact_list.pop()
    elif defect == "expired":
        client.artifact_list[0]["expired"] = True
    else:
        client.artifact_list[0]["workflow_run"]["id"] = 99
    with pytest.raises(VerificationError):
        reconcile.plan(client, repository="platform", site_repository="site")
    assert calls["rendered"] == []


def test_recovered_candidate_still_has_to_pass_attestation(planned, monkeypatch):
    client, calls = planned
    original = reconcile._attested_builds

    def reject(repository_client, repository, sha):
        if sha == "h2":
            raise VerificationError("attestation rejected")
        return original(repository_client, repository, sha)

    monkeypatch.setattr(reconcile, "_attested_builds", reject)
    with pytest.raises(VerificationError, match="attestation rejected"):
        reconcile.plan(client, repository="platform", site_repository="site")
    assert calls["rendered"] == []


def test_recovery_does_not_bypass_released_source_ancestry(planned):
    client, calls = planned
    client.comparison = "diverged"
    with pytest.raises(VerificationError, match="not an ancestor"):
        reconcile.plan(client, repository="platform", site_repository="site")
    assert calls["attested"] == [] and calls["rendered"] == []


def test_artifacts_disappearing_after_selection_still_fail_full_build_requirement(planned, monkeypatch):
    client, calls = planned
    original = reconcile._attested_builds

    def incomplete(repository_client, repository, sha):
        builds = original(repository_client, repository, sha)
        if sha == "h2":
            builds.pop("agent")
        return builds

    monkeypatch.setattr(reconcile, "_attested_builds", incomplete)
    with pytest.raises(VerificationError, match="missing agent"):
        reconcile.plan(client, repository="platform", site_repository="site")
    assert calls["rendered"] == []


def test_recovered_sources_still_require_authorisation(planned, monkeypatch):
    client, _ = planned

    def reject(*_args, **_kwargs):
        raise VerificationError("source not authorised")

    monkeypatch.setattr(reconcile, "verify_release", reject)
    with pytest.raises(VerificationError, match="source not authorised"):
        reconcile.plan(client, repository="platform", site_repository="site")
