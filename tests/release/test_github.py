import pytest

from scripts.release.github import GitHubClient, GitHubError


def test_artifact_lookup_uses_repository_artifacts_endpoint():
    client = GitHubClient("token")
    calls = []
    client.request = lambda method, path, body=None, **kwargs: calls.append((method, path)) or {"artifacts": []}

    client.artifacts("owner/repo")

    assert calls == [(
        "GET",
        "/repos/owner/repo/actions/artifacts?per_page=100",
    )]


def test_missing_reads_only_ignore_not_found():
    client = GitHubClient("token")

    def not_found(*args, **kwargs):
        raise GitHubError("missing", status_code=404)

    client.request = not_found
    assert client.read("owner/repo", "releases", "records/1/record.json", allow_missing=True) is None
    assert client.last_record("owner/repo", "releases") is None

    def forbidden(*args, **kwargs):
        raise GitHubError("forbidden", status_code=403)

    client.request = forbidden
    with pytest.raises(GitHubError):
        client.read("owner/repo", "releases", "records/1/record.json", allow_missing=True)
    with pytest.raises(GitHubError):
        client.last_record("owner/repo", "releases")


def test_commit_files_uses_current_tree_and_commit_as_parent():
    client = GitHubClient("token")
    calls = []

    def request(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if method == "GET":
            return {"tree": {"sha": "current-tree"}}
        if path.endswith("/git/blobs"):
            return {"sha": "blob-sha"}
        if path.endswith("/git/trees"):
            assert body["base_tree"] == "current-tree"
            return {"sha": "new-tree"}
        if path.endswith("/git/commits"):
            assert body["tree"] == "new-tree"
            assert body["parents"] == ["current-commit"]
            return {"sha": "new-commit"}
        assert method == "PATCH"
        assert body == {"sha": "new-commit", "force": False}
        return None

    client.request = request
    assert client.commit_files("owner/repo", "releases", "current-commit", {"record.json": b"{}"}, "publish") == "new-commit"
    assert calls[0][1].endswith("/git/commits/current-commit")
