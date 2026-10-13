import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.release import reconcile
from scripts.release.verify import VerificationError, VerificationResult


class Client:
    def __init__(self, previous, heads):
        self.previous = previous
        self.heads = heads

    def last_record(self, _repository, _branch):
        return self.previous

    def ref(self, repository, _branch):
        if repository == "site":
            return self.heads["homelab-site"]
        if repository.endswith("tracker-integrations"):
            return self.heads["tracker-integrations"]
        return self.heads[repository]


def test_sign_plan_record_images_equal_bundle_images(monkeypatch, tmp_path):
    site = Path(__file__).parent / "fixtures" / "site"
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name in ("compose.json", "manifest.yaml", "probes.json", "policy.yaml"):
        shutil.copy2(site / name, inputs / name)
    shutil.copytree(site / "files", inputs / "files")
    monkeypatch.setenv("RELEASE_INPUTS_DIR", str(inputs))

    image_refs = {
        "alpha": "ghcr.io/jon-robbins/homelab/homelab-agent@sha256:" + "a" * 64,
        "beta": "ghcr.io/jon-robbins/homelab/homelab-mcp@sha256:" + "b" * 64,
    }
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "release": True,
        "site_id": "lab",
        "sequence": 1,
        "sha": "tooling",
        "sources": {"homelab": "tooling", "homelab-site": "site", "tracker-integrations": "tracker"},
        "image_refs": image_refs,
    }))
    signed = {}

    def fake_sign_record(record, destination):
        signed["record"] = record
        return b"signature"

    monkeypatch.setattr(reconcile, "sign_record", fake_sign_record)
    reconcile.sign_plan(plan, tmp_path / "output")

    with tarfile.open(tmp_path / "output" / "bundle.tar", "r") as bundle:
        applications = json.loads(bundle.extractfile("applications.json").read())
    bundle_images = {item["name"]: item["image"] for item in applications["applications"]}
    assert signed["record"]["images"] == bundle_images == image_refs


def _record(sources, images, *, rollback_floor=4):
    return {
        "sequence": 7,
        "sources": sources,
        "images": images,
        "rollback_floor": rollback_floor,
    }


def _setup(monkeypatch, images):
    monkeypatch.setenv("GITHUB_SHA", "h2")
    monkeypatch.setattr(reconcile, "load_approved_destinations", lambda: {
        f"ghcr.io/jon-robbins/homelab/{image}" for image in images
    })
    monkeypatch.setattr(reconcile, "_materialize_site_inputs", lambda *_args: None)
    monkeypatch.setattr(reconcile, "_manifest_site_id", lambda _root: "lab")
    monkeypatch.setattr(
        reconcile,
        "verify_release",
        lambda _client, _sources, builds, **_kwargs: VerificationResult(
            (), {image: spec["manifest_digest"] for image, spec in builds.items()}, dict(builds)
        ),
    )


def _build(image, digest):
    return {
        "repository": "homelab",
        "head_sha": "h2",
        "run_id": 42,
        "artifact_name": f"oci-{image}",
        "image": image,
        "manifest_digest": digest,
    }


def test_carries_forward_unchanged_images(monkeypatch, tmp_path):
    images = {"agent", "mcp"}
    previous = _record(
        {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"},
        {"agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64,
         "mcp": "ghcr.io/jon-robbins/homelab/mcp@sha256:" + "b" * 64},
    )
    client = Client(previous, {"homelab": "h2", "homelab-site": "s1", "tracker-integrations": "t1"})
    _setup(monkeypatch, images)
    monkeypatch.setattr(reconcile, "_attested_builds", lambda *_args: {"agent": _build("agent", "sha256:" + "c" * 64)})

    result = reconcile.plan(client, repository="homelab", site_repository="site", output=tmp_path / "plan.json")

    assert result["image_refs"]["mcp"] == previous["images"]["mcp"]
    assert result["image_refs"]["agent"].endswith("@sha256:" + "c" * 64)
    assert result["rollback_floor"] == 4


def test_first_release_requires_every_approved_image(monkeypatch):
    images = {"agent", "mcp"}
    client = Client(None, {"homelab": "h2", "homelab-site": "s1", "tracker-integrations": "t1"})
    _setup(monkeypatch, images)
    monkeypatch.setattr(reconcile, "_attested_builds", lambda *_args: {"agent": _build("agent", "sha256:" + "a" * 64)})

    with pytest.raises(VerificationError, match="first release requires verified builds for all images"):
        reconcile.plan(client, repository="homelab", site_repository="site")


def test_site_only_change_preserves_full_image_map(monkeypatch):
    images = {"agent", "mcp"}
    image_map = {
        "agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64,
        "mcp": "ghcr.io/jon-robbins/homelab/mcp@sha256:" + "b" * 64,
    }
    sources = {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"}
    previous = _record(sources, image_map)
    client = Client(previous, {"homelab": "h1", "homelab-site": "s2", "tracker-integrations": "t1"})
    _setup(monkeypatch, images)
    monkeypatch.setenv("GITHUB_SHA", "h1")

    result = reconcile.plan(client, repository="homelab", site_repository="site")

    assert result["release"] is True
    assert result["images"] == []
    assert result["image_refs"] == image_map
    assert result["site_id"] == "lab"


def test_homelab_only_change_without_image_build_preserves_full_image_map(monkeypatch):
    images = {"agent", "mcp"}
    image_map = {
        "agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64,
        "mcp": "ghcr.io/jon-robbins/homelab/mcp@sha256:" + "b" * 64,
    }
    sources = {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"}
    previous = _record(sources, image_map)
    client = Client(previous, {"homelab": "h2", "homelab-site": "s1", "tracker-integrations": "t1"})
    _setup(monkeypatch, images)
    monkeypatch.setattr(reconcile, "_attested_builds", lambda *_args: {})

    result = reconcile.plan(client, repository="homelab", site_repository="site")

    assert result["release"] is True
    assert result["images"] == []
    assert result["image_refs"] == image_map


class HistoryClient(Client):
    def __init__(self, previous, heads, history):
        super().__init__(previous, heads)
        self.history = history

    def compare_commits(self, _repository, base, head):
        sha = head
        while sha in self.history:
            if sha == base:
                return {"status": "ahead"}
            parents = self.history[sha]["parents"]
            if not parents:
                break
            sha = parents[0]["sha"]
        return {"status": "behind"}

    def read_commit(self, _repository, sha):
        return self.history[sha]


def test_first_release_bootstraps_at_run_head(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "h3")
    history = {
        "h3": {"parents": [{"sha": "h2"}]},
        "h2": {"parents": [{"sha": "h1"}]},
        "h1": {"parents": []},
    }
    assert reconcile._next_tooling_sha(HistoryClient(None, {"homelab": "h3"}, history), "homelab", "h3", None) == "h3"


def test_second_run_selects_oldest_commit_after_previous_position(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "h3")
    history = {
        "h3": {"parents": [{"sha": "h2"}]},
        "h2": {"parents": [{"sha": "h1"}]},
        "h1": {"parents": [{"sha": "h0"}]},
        "h0": {"parents": []},
    }
    previous = _record({"homelab": "h1"}, {})
    client = HistoryClient(previous, {"homelab": "h3"}, history)
    assert reconcile._next_tooling_sha(client, "homelab", "h3", previous) == "h2"


def test_two_unreleased_merges_are_released_fifo_with_commit_images(monkeypatch):
    images = {"agent"}
    previous = _record(
        {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"},
        {"agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64},
    )
    history = {
        "h3": {"parents": [{"sha": "h2"}]},
        "h2": {"parents": [{"sha": "h1"}]},
        "h1": {"parents": [{"sha": "h0"}]},
        "h0": {"parents": []},
    }
    client = HistoryClient(previous, {"homelab": "h3", "homelab-site": "s1", "tracker-integrations": "t1"}, history)
    _setup(monkeypatch, images)
    monkeypatch.setenv("GITHUB_SHA", "h3")
    monkeypatch.setattr(reconcile, "_attested_builds", lambda _client, _repository, sha: {
        "agent": {**_build("agent", "sha256:" + sha[-1] * 64), "head_sha": sha}
    })

    first = reconcile.plan(client, repository="homelab", site_repository="site")
    assert first["sha"] == "h2"
    assert first["images"][0]["manifest_digest"] == "sha256:" + "2" * 64

    client.previous = {**previous, "sequence": 8, "tooling_git_sha": "h2", "sources": {**previous["sources"], "homelab": "h2"}}
    second = reconcile.plan(client, repository="homelab", site_repository="site")
    assert second["sha"] == "h3"
    assert second["images"][0]["manifest_digest"] == "sha256:" + "3" * 64


def test_live_main_ahead_of_run_commit_does_not_change_plan(monkeypatch):
    previous = _record(
        {"homelab": "h2", "homelab-site": "s1", "tracker-integrations": "t1"},
        {"agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64},
    )
    history = {
        "h4": {"parents": [{"sha": "h3"}]},
        "h3": {"parents": [{"sha": "h2"}]},
        "h2": {"parents": [{"sha": "h1"}]},
        "h1": {"parents": [{"sha": "h0"}]},
        "h0": {"parents": []},
    }
    client = HistoryClient(previous, {"homelab": "h4", "homelab-site": "s1", "tracker-integrations": "t1"}, history)
    _setup(monkeypatch, {"agent"})
    monkeypatch.setenv("GITHUB_SHA", "h3")
    monkeypatch.setattr(reconcile, "_attested_builds", lambda _client, _repository, sha: {
        "agent": {**_build("agent", "sha256:" + sha[-1] * 64), "head_sha": sha}
    })

    result = reconcile.plan(client, repository="homelab", site_repository="site")

    assert result["sha"] == "h3"


def test_plan_requires_run_commit():
    client = Client(None, {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"})

    with pytest.raises(VerificationError, match="requires GITHUB_SHA"):
        reconcile.plan(client, repository="homelab", site_repository="site")


def test_rewritten_history_rejects_release():
    history = {"h3": {"parents": [{"sha": "h2"}]}, "h2": {"parents": []}}
    previous = _record({"homelab": "old"}, {})
    client = HistoryClient(previous, {"homelab": "h3"}, history)
    with pytest.raises(VerificationError, match="not an ancestor"):
        reconcile._next_tooling_sha(client, "homelab", "h3", previous)


class BuildStatusClient:
    def __init__(self, runs):
        self.runs = runs

    def workflow_runs(self, *_args):
        return {"workflow_runs": self.runs}

    def artifacts(self, *_args):
        raise AssertionError("artifacts must not be consulted before an exact successful run exists")


class PlanBuildStatusClient(Client):
    def workflow_runs(self, *_args):
        return {"workflow_runs": []}

    def artifacts(self, *_args):
        raise AssertionError("artifacts must not be consulted before an exact successful run exists")


def test_non_push_success_does_not_satisfy_pending_push_build():
    runs = [
        {"id": 99, "head_sha": "h2", "event": "workflow_dispatch", "head_branch": "main",
         "status": "completed", "conclusion": "success"},
        {"id": 42, "head_sha": "h2", "event": "push", "head_branch": "main",
         "status": "in_progress", "conclusion": None},
    ]
    with pytest.raises(VerificationError, match="still running or failed"):
        reconcile._attested_builds(BuildStatusClient(runs), "homelab", "h2")


class ArtifactFixtureClient:
    def workflow_runs(self, *_args):
        return {"workflow_runs": [{"id": 42, "head_sha": "h2", "event": "push", "head_branch": "main",
                                    "status": "completed", "conclusion": "success"}]}

    def artifacts(self, *_args):
        return json.loads((Path(__file__).parent / "fixtures" / "artifacts.json").read_text())

    def download_artifact_member(self, _repository, artifact, member, directory):
        assert member == "oci-layout.tar"
        target = directory / member
        target.write_bytes(b"archive")
        return target


def test_artifacts_are_bound_to_successful_workflow_run_fixture(monkeypatch):
    monkeypatch.setattr(reconcile, "_verify_attestation", lambda *_args: None)
    monkeypatch.setattr(reconcile, "_read_manifest_digest", lambda _archive: "sha256:" + "a" * 64)

    builds = reconcile._attested_builds(ArtifactFixtureClient(), "homelab", "h2")

    assert builds["agent"]["run_id"] == 42


@pytest.mark.parametrize("runs, message", [
    ([], "not visible yet"),
    ([{"head_sha": "h2", "event": "push", "head_branch": "main", "status": "completed", "conclusion": "failure"}], "still running or failed"),
    ([{"head_sha": "h2", "event": "push", "head_branch": "main", "status": "completed", "conclusion": "success",
       "path": ".github/workflows/ci.yml"}], "not visible yet"),
])
def test_pending_or_failed_post_merge_build_waits(runs, message):
    with pytest.raises(VerificationError, match=message):
        reconcile._attested_builds(BuildStatusClient(runs), "homelab", "h2")


def test_plan_waits_without_release_for_unseen_candidate_build(monkeypatch, tmp_path):
    image_map = {"agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64}
    previous = _record(
        {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"},
        image_map,
    )
    client = PlanBuildStatusClient(
        previous,
        {"homelab": "h2", "homelab-site": "s1", "tracker-integrations": "t1"},
    )
    _setup(monkeypatch, {"agent"})
    output = tmp_path / "plan.json"

    with pytest.raises(VerificationError, match="not visible yet"):
        reconcile.plan(client, repository="homelab", site_repository="site", output=output)

    assert not output.exists()


def test_no_source_change_produces_no_release(monkeypatch):
    sources = {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"}
    image_map = {"agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64}
    previous = _record(sources, image_map)
    client = Client(previous, sources)
    _setup(monkeypatch, {"agent"})
    monkeypatch.setenv("GITHUB_SHA", "h1")

    result = reconcile.plan(client, repository="homelab", site_repository="site")

    assert result == {
        "release": False,
        "sha": "h1",
        "sequence": 8,
        "images": [],
        "image_refs": image_map,
    }


def test_reads_manifest_digest_with_skopeo(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="sha256:" + "a" * 64 + "\n", stderr="")

    monkeypatch.setattr(reconcile.subprocess, "run", fake_run)
    assert reconcile._read_manifest_digest(b"archive") == "sha256:" + "a" * 64
    assert calls[0][0][:4] == ["skopeo", "inspect", "--format", "{{.Digest}}"]
    assert calls[0][0][4].startswith("oci-archive:")


def test_verifies_archive_attestation_with_gh(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="verified\n", stderr="")

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(reconcile.subprocess, "run", fake_run)
    reconcile._verify_attestation(b"archive", "owner/repo", "a" * 40)

    command, kwargs = calls[0]
    assert command[:3] == ["gh", "attestation", "verify"]
    assert command[4:] == [
        "--repo", "owner/repo",
        "--signer-workflow", "owner/repo/.github/workflows/post-merge-build.yml",
        "--source-digest", "a" * 40,
    ]
    assert "check" not in kwargs


def test_plan_denies_unapproved_verified_destination(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "h2")
    previous = _record(
        {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"},
        {"agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "b" * 64},
    )
    client = Client(previous, {"homelab": "h2", "homelab-site": "s1", "tracker-integrations": "t1"})
    monkeypatch.setattr(
        reconcile,
        "load_approved_destinations",
        lambda: {"ghcr.io/jon-robbins/homelab/agent"},
    )
    monkeypatch.setattr(reconcile, "_materialize_site_inputs", lambda *_args: None)
    monkeypatch.setattr(reconcile, "_manifest_site_id", lambda _root: "lab")
    monkeypatch.setattr(
        reconcile,
        "_attested_builds",
        lambda *_args: {"rogue": _build("rogue", "sha256:" + "a" * 64)},
    )
    monkeypatch.setattr(
        reconcile,
        "verify_release",
        lambda *_args, **_kwargs: VerificationResult(
            (), {"rogue": "sha256:" + "a" * 64}, {"rogue": _build("rogue", "sha256:" + "a" * 64)},
        ),
    )

    with pytest.raises(VerificationError, match="unapproved destination"):
        reconcile.plan(client, repository="homelab", site_repository="site")


def test_approved_destinations_loader_remains_strict(tmp_path):
    config = tmp_path / "approved.json"
    config.write_text('{"destinations": ["ghcr.io/acme/homelab/web"]}')
    assert reconcile.load_approved_destinations(config) == {"ghcr.io/acme/homelab/web"}
    config.write_text('{"destinations": ["docker.io/acme/web"]}')
    with pytest.raises(ValueError, match="immutable GHCR repository"):
        reconcile.load_approved_destinations(config)
