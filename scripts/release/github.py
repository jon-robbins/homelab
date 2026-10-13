"""Small, dependency-free GitHub REST client used by the release reconciler."""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zipfile import ZipFile
from io import BytesIO


class GitHubError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    def __init__(self, token: str | None = None, *, api: str = "https://api.github.com") -> None:
        self.token = token or os.environ.get("PRIVATE_INPUT_READ_TOKEN") or os.environ.get("SITE_FEED_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise GitHubError("a GitHub token is required")
        self.api = api.rstrip("/")

    def _request(self, method: str, path: str, body: Any = None, *, accept: str = "application/vnd.github+json") -> tuple[Any, Mapping[str, str]]:
        headers = {"Accept": accept, "Authorization": f"Bearer {self.token}", "X-GitHub-Api-Version": "2022-11-28"}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = Request(path if path.startswith("https://") else self.api + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request) as response:
                raw = response.read()
                response_headers = {key: value for key, value in response.headers.items()}
        except HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise GitHubError(f"GitHub API {error.code} {method} {path}: {detail[:300]}", status_code=error.code) from error
        if not raw:
            return None, response_headers
        try:
            return json.loads(raw), response_headers
        except (json.JSONDecodeError, UnicodeDecodeError):
            return raw, response_headers

    def request(self, method: str, path: str, body: Any = None, *, accept: str = "application/vnd.github+json") -> Any:
        return self._request(method, path, body, accept=accept)[0]

    @staticmethod
    def _repo(repository: str) -> str:
        return quote(repository, safe="/")

    def read_commit(self, repository: str, sha: str) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}/commits/{sha}")

    def pull_requests_for_commit(self, repository: str, sha: str) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}/commits/{sha}/pulls")

    def rulesets(self, repository: str) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}/rulesets?includes_parents=true")

    def ruleset(self, repository: str, ruleset_id: int) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}/rulesets/{ruleset_id}")

    def compare_commits(self, repository: str, base: str, head: str) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}/compare/{quote(base, safe='')}...{quote(head, safe='')}")

    def commits(self, repository: str, branch: str) -> list[Mapping[str, Any]]:
        path = f"/repos/{self._repo(repository)}/commits?sha={quote(branch, safe='')}&per_page=100"
        commits: list[Mapping[str, Any]] = []
        while path:
            value, headers = self._request("GET", path)
            if isinstance(value, list):
                commits.extend(item for item in value if isinstance(item, Mapping))
            path = self._next_link(headers.get("Link"))
        return commits

    @staticmethod
    def _next_link(header: str | None) -> str:
        if not header:
            return ""
        for link in header.split(","):
            match = re.match(r"\s*<([^>]+)>\s*;\s*rel=\"next\"", link)
            if match:
                return match.group(1)
        return ""

    def artifacts(self, repository: str) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}/actions/artifacts?per_page=100")

    def workflow_runs(self, repository: str, workflow: str, sha: str, *, event: str = "push", branch: str = "main") -> Any:
        return self.request(
            "GET",
            f"/repos/{self._repo(repository)}/actions/workflows/{quote(workflow, safe='/')}/runs"
            f"?head_sha={quote(sha, safe='')}&event={quote(event, safe='')}&branch={quote(branch, safe='')}&per_page=100",
        )

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

    def repository(self, repository: str) -> Any:
        return self.request("GET", f"/repos/{self._repo(repository)}")

    def read(self, repository: str, branch: str, path: str, allow_missing: bool = False) -> bytes | None:
        try:
            value = self.request("GET", f"/repos/{self._repo(repository)}/contents/{quote(path, safe='/')}?ref={quote(branch, safe='')}")
        except GitHubError as error:
            if allow_missing and error.status_code == 404:
                return None
            raise
        return base64.b64decode(value["content"])

    def contents(self, repository: str, ref: str, path: str = "") -> Any:
        suffix = f"/{quote(path, safe='/')}" if path else ""
        return self.request("GET", f"/repos/{self._repo(repository)}/contents{suffix}?ref={quote(ref, safe='')}")

    read_file = read

    def last_record(self, repository: str, branch: str) -> Mapping[str, Any] | None:
        try:
            listing = self.request("GET", f"/repos/{self._repo(repository)}/contents/records?ref={quote(branch, safe='')}")
        except GitHubError as error:
            if error.status_code == 404:
                return None
            raise
        sequences = [int(item["name"]) for item in listing if item.get("type") == "dir" and item.get("name", "").isdigit()]
        if not sequences:
            return None
        from .contracts import load_release_record
        return load_release_record(self.read(repository, branch, f"records/{max(sequences)}/record.json") or b"")

    def published_tooling_shas(self, repository: str, branch: str) -> set[str]:
        try:
            listing = self.request("GET", f"/repos/{self._repo(repository)}/contents/records?ref={quote(branch, safe='')}")
        except GitHubError as error:
            if error.status_code == 404:
                return set()
            raise
        shas: set[str] = set()
        for item in listing if isinstance(listing, list) else ():
            name = item.get("name", "")
            if item.get("type") != "dir" or not name.isdigit():
                continue
            data = self.read(repository, branch, f"records/{name}/record.json", allow_missing=True)
            if data:
                from .contracts import load_release_record
                shas.add(load_release_record(data)["tooling_git_sha"])
        return shas

    def commit_files(self, repository: str, branch: str, ref: str, files: Mapping[str, bytes], message: str) -> str:
        commit = self.request("GET", f"/repos/{self._repo(repository)}/git/commits/{quote(ref, safe='')}")
        base_tree = commit["tree"]["sha"]
        blobs = []
        for path, data in files.items():
            blob = self.request("POST", f"/repos/{self._repo(repository)}/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            blobs.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self.request("POST", f"/repos/{self._repo(repository)}/git/trees", {"base_tree": base_tree, "tree": blobs})
        commit = self.request("POST", f"/repos/{self._repo(repository)}/git/commits", {"message": message, "tree": tree["sha"], "parents": [ref]})
        try:
            self.request("PATCH", f"/repos/{self._repo(repository)}/git/refs/heads/{quote(branch, safe='')}", {"sha": commit["sha"], "force": False})
        except GitHubError as error:
            raise RuntimeError("CAS ref changed") from error
        return commit["sha"]
