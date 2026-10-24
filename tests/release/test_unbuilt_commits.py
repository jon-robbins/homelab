import pytest

from scripts.release import reconcile
from scripts.release.verify import VerificationError


class CommitClient:
    """One commit with no post-merge push run, as a GITHUB_TOKEN merge leaves it."""

    def __init__(self, files, *, runs=(), parents=1):
        self.commit = {"files": [{"filename": name} for name in files],
                       "parents": [{"sha": f"p{index}"} for index in range(parents)]}
        self.runs = list(runs)

    def workflow_runs(self, _repository, _workflow, _sha):
        return {"workflow_runs": self.runs}

    def read_commit(self, _repository, _sha):
        return self.commit

    def artifacts(self, _repository):
        return {"artifacts": []}


def test_commit_without_build_inputs_needs_no_build():
    client = CommitClient(["modules/app/compose.yaml"])
    assert reconcile._attested_builds(client, "homelab", "h1") == {}


@pytest.mark.parametrize("path", [
    "apps/homelab-workers/main.py",
    "config/caddy/Dockerfile",
    ".github/workflows/post-merge-build.yml",
    "config/release/approved-ghcr-destinations.json",
])
def test_commit_with_build_inputs_waits_for_its_build(path):
    client = CommitClient(["modules/app/compose.yaml", path])
    with pytest.raises(VerificationError, match="not visible yet"):
        reconcile._attested_builds(client, "homelab", "h1")


def test_renamed_build_input_waits_for_its_build():
    client = CommitClient([])
    client.commit["files"] = [{"filename": "docs/caddy.md", "previous_filename": "config/caddy/README.md"}]
    with pytest.raises(VerificationError, match="not visible yet"):
        reconcile._attested_builds(client, "homelab", "h1")


def test_truncated_file_list_waits_for_its_build():
    client = CommitClient([f"docs/{index}.md" for index in range(reconcile.COMMIT_FILES_LIMIT)])
    with pytest.raises(VerificationError, match="not visible yet"):
        reconcile._attested_builds(client, "homelab", "h1")


def test_merge_commit_waits_for_its_build():
    client = CommitClient(["modules/app/compose.yaml"], parents=2)
    with pytest.raises(VerificationError, match="not visible yet"):
        reconcile._attested_builds(client, "homelab", "h1")


def test_running_build_is_still_awaited():
    run = {"id": 1, "head_sha": "h1", "event": "push", "head_branch": "main",
           "path": reconcile.BUILD_WORKFLOW, "status": "in_progress", "conclusion": None}
    client = CommitClient(["modules/app/compose.yaml"], runs=[run])
    with pytest.raises(VerificationError, match="still running"):
        reconcile._attested_builds(client, "homelab", "h1")
