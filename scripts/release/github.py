"""Small, dependency-free GitHub REST client used by the release reconciler."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zipfile import ZipFile
from io import BytesIO


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, *, api: str = "https://api.github.com") -> None:
        self.token = token or os.environ.get("PRIVATE_INPUT_READ_TOKEN") or os.environ.get("SITE_FEED_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise GitHubError("a GitHub token is required")
        self.api = api.rstrip("/")

    def request(self, method: str, path: str, body: Any = None, *, accept: str = "application/vnd.github+json") -> Any:
        headers = {"Accept": accept, "Authorization": f"Bearer {self.token}", "X-GitHub-Api-Version": "2022-11-28"}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = Request(self.api + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request) as response:
                raw = response.read()
        except HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise GitHubError(f"GitHub API {error.code} {method} {path}: {detail[:300]}") from error
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @staticmethod
    def _repo(repository: str) -> str:
        return quote(repository, safe="/")

    def commit(self, repository: str, sha: str) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}/commits/{sha}")

    def pull_requests_for_commit(self, repository: str, sha: str) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}/commits/{sha}/pulls")

    def workflow_runs(self, repository: str, workflow: str, head_sha: str) -> Any:
        path = f"/repos/{self._repo(repository)}/actions/workflows/{quote(workflow, safe='/')}/runs?head_sha={head_sha}&per_page=100"
        return self.request("GET", path)

    def run_artifacts(self, repository: str, run_id: int) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}/actions/runs/{run_id}/artifacts?per_page=100")

    def artifact_zip(self, repository: str, artifact_id: int) -> bytes:
        return self.request("GET", f"/repos/{self._repo(repository)}/actions/artifacts/{artifact_id}/zip", accept="application/octet-stream")

    def artifact_files(self, repository: str, artifact: Mapping[str, Any]) -> dict[str, bytes]:
        raw = self.artifact_zip(repository, int(artifact["id"]))
        if not isinstance(raw, bytes):
            raise GitHubError("artifact download was not binary")
        with ZipFile(BytesIO(raw)) as archive:
            return {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}

    def ref(self, repository: str, branch: str) -> str:
        value = self.request("GET", f"/repos/{self._repo(repository)}/git/ref/heads/{quote(branch, safe='')}")
        return value["object"]["sha"]

    branch_ref = ref

    def read(self, repository: str, branch: str, path: str, allow_missing: bool = False) -> bytes | None:
        try:
            value = self.request("GET", f"/repos/{self._repo(repository)}/contents/{quote(path, safe='/')}?ref={quote(branch, safe='')}")
        except GitHubError:
            if allow_missing:
                return None
            raise
        return base64.b64decode(value["content"])

    read_file = read

    def last_record(self, repository: str, branch: str) -> Mapping[str, Any] | None:
        try:
            listing = self.request("GET", f"/repos/{self._repo(repository)}/contents/records?ref={quote(branch, safe='')}")
        except GitHubError:
            return None
        sequences = [int(item["name"]) for item in listing if item.get("type") == "dir" and item.get("name", "").isdigit()]
        if not sequences:
            return None
        from .contracts import load_release_record
        return load_release_record(self.read(repository, branch, f"records/{max(sequences)}/record.json") or b"")

    def commit_files(self, repository: str, branch: str, ref: str, files: Mapping[str, bytes], message: str) -> str:
        blobs = []
        for path, data in files.items():
            blob = self.request("POST", f"/repos/{self._repo(repository)}/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            blobs.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self.request("POST", f"/repos/{self._repo(repository)}/git/trees", {"base_tree": ref, "tree": blobs})
        commit = self.request("POST", f"/repos/{self._repo(repository)}/git/commits", {"message": message, "tree": tree["sha"], "parents": [ref]})
        try:
            self.request("PATCH", f"/repos/{self._repo(repository)}/git/refs/heads/{quote(branch, safe='')}", {"sha": commit["sha"], "force": False})
        except GitHubError as error:
            raise RuntimeError("CAS ref changed") from error
        return commit["sha"]

