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


def test_workflow_runs_uses_workflow_file_name():
    client = GitHubClient("token")
    calls = []
    client.request = lambda method, path, body=None, **kwargs: calls.append((method, path)) or {"workflow_runs": []}

    client.workflow_runs("owner/repo", ".github/workflows/post-merge-build.yml", "a" * 40)

    assert calls == [(
        "GET",
        "/repos/owner/repo/actions/workflows/post-merge-build.yml/runs"
        f"?head_sha={'a' * 40}&event=push&branch=main&per_page=100",
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


def test_artifact_download_never_sends_token_to_storage(monkeypatch, tmp_path):
    import io
    import zipfile
    from email.message import Message
    from urllib.error import HTTPError

    from scripts.release import github

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("oci-layout.tar", b"layout-bytes")
    storage_requests = []

    class Opener:
        def open(self, request):
            assert request.get_header("Authorization") == "Bearer token"
            assert request.get_header("Accept") == "application/vnd.github+json"
            headers = Message()
            headers["Location"] = "https://storage.example/artifact.zip?sig=x"
            raise HTTPError(request.full_url, 302, "Found", headers, io.BytesIO())

    def storage_open(request):
        storage_requests.append(request)
        return io.BytesIO(payload.getvalue())

    monkeypatch.setattr(github, "build_opener", lambda *_handlers: Opener())
    monkeypatch.setattr(github, "urlopen", storage_open)

    client = GitHubClient("token")
    path = client.download_artifact_member("owner/repo", {"id": 9, "name": "oci-caddy"}, "oci-layout.tar", tmp_path)

    assert path.read_bytes() == b"layout-bytes"
    assert [request.full_url for request in storage_requests] == ["https://storage.example/artifact.zip?sig=x"]
    assert storage_requests[0].get_header("Authorization") is None
    assert not (tmp_path / "artifact.zip").exists()


def test_artifact_download_requires_a_redirect(monkeypatch, tmp_path):
    import io
    from email.message import Message
    from urllib.error import HTTPError

    from scripts.release import github

    class Opener:
        def open(self, request):
            raise HTTPError(request.full_url, 415, "Unsupported", Message(), io.BytesIO(b'{"message":"no"}'))

    monkeypatch.setattr(github, "build_opener", lambda *_handlers: Opener())
    with pytest.raises(GitHubError, match="415"):
        GitHubClient("token").download_artifact_member("owner/repo", {"id": 9}, "oci-layout.tar", tmp_path)


def test_pull_requests_for_commit_reads_full_records_for_merged_by():
    client = GitHubClient("token")
    sha = "b" * 40
    calls = []
    responses = {
        f"/repos/owner/site/commits/{sha}/pulls": [
            {"number": 2, "merged_at": "2026-10-12T13:40:30Z", "merge_commit_sha": sha},
            {"number": 3, "merged_at": None, "merge_commit_sha": sha},
            {"number": 4, "merged_at": "2026-10-12T13:40:30Z", "merge_commit_sha": "c" * 40},
        ],
        "/repos/owner/site/pulls/2": {"number": 2, "merged_by": {"login": "owner"}, "merge_commit_sha": sha},
    }

    def request(method, path, body=None, **kwargs):
        calls.append(path)
        return responses[path]

    client.request = request
    prs = client.pull_requests_for_commit("owner/site", sha)

    assert prs == [{"number": 2, "merged_by": {"login": "owner"}, "merge_commit_sha": sha}]
    assert calls == [f"/repos/owner/site/commits/{sha}/pulls", "/repos/owner/site/pulls/2"]
