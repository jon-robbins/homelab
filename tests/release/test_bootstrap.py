import pytest

from scripts.release import reconcile
from scripts.release.verify import VerificationError

IMAGES = {"caddy", "agent"}


class BootstrapClient:
    """History h3 -> h2 -> h1 -> h0 with per-commit build runs and artifacts."""

    def __init__(self, runs, artifacts):
        self.runs = runs
        self.artifact_list = artifacts
        self.parents = {"h3": "h2", "h2": "h1", "h1": "h0", "h0": None}

    def workflow_runs(self, _repository, _workflow, sha):
        return {"workflow_runs": self.runs.get(sha, [])}

    def artifacts(self, _repository):
        return {"artifacts": self.artifact_list}

    def read_commit(self, _repository, sha):
        parent = self.parents[sha]
        return {"parents": [{"sha": parent}] if parent else []}


def _run(run_id, sha, conclusion="success"):
    return {"id": run_id, "head_sha": sha, "event": "push", "head_branch": "main",
            "path": reconcile.BUILD_WORKFLOW, "status": "completed", "conclusion": conclusion}


def _artifacts(run_id, sha, images):
    return [{"name": f"oci-{image}", "workflow_run": {"id": run_id, "head_sha": sha}} for image in images]


def test_bootstrap_uses_head_when_head_has_a_full_build():
    client = BootstrapClient({"h3": [_run(3, "h3")]}, _artifacts(3, "h3", IMAGES))
    assert reconcile._bootstrap_sha(client, "homelab", "h3", IMAGES) == "h3"


def test_bootstrap_skips_later_merges_without_builds():
    runs = {"h3": [_run(3, "h3")], "h2": [_run(2, "h2")], "h1": [_run(1, "h1")]}
    artifacts = _artifacts(2, "h2", {"agent"}) + _artifacts(1, "h1", IMAGES)
    client = BootstrapClient(runs, artifacts)
    assert reconcile._bootstrap_sha(client, "homelab", "h3", IMAGES) == "h1"


def test_bootstrap_ignores_failed_runs_and_artifacts_from_other_runs():
    runs = {"h3": [_run(3, "h3", conclusion="failure")], "h2": [_run(2, "h2")]}
    artifacts = _artifacts(3, "h3", IMAGES) + _artifacts(99, "h2", IMAGES)
    client = BootstrapClient(runs, artifacts)
    with pytest.raises(VerificationError, match="first release requires one main commit"):
        reconcile._bootstrap_sha(client, "homelab", "h3", IMAGES)


def test_bootstrap_ignores_expired_artifacts():
    artifacts = [{**item, "expired": True} for item in _artifacts(3, "h3", IMAGES)]
    client = BootstrapClient({"h3": [_run(3, "h3")]}, artifacts)
    with pytest.raises(VerificationError, match="rebuild every image"):
        reconcile._bootstrap_sha(client, "homelab", "h3", IMAGES)


def test_bootstrap_search_is_bounded(monkeypatch):
    monkeypatch.setattr(reconcile, "BOOTSTRAP_SEARCH_DEPTH", 2)
    client = BootstrapClient({"h1": [_run(1, "h1")]}, _artifacts(1, "h1", IMAGES))
    with pytest.raises(VerificationError):
        reconcile._bootstrap_sha(client, "homelab", "h3", IMAGES)
