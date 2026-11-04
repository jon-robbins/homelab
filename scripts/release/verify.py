"""Offline-testable authority checks for release inputs.

The client is deliberately tiny: callers provide an object with methods for
``read_commit`` and ``pull_requests_for_commit``.  The methods may return
dicts or objects with the same attribute names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class VerificationError(ValueError):
    """A release input is not authoritative."""


class GitHubClient(Protocol):
    def read_commit(self, repository: str, sha: str) -> Any: ...
    def pull_requests_for_commit(self, repository: str, sha: str) -> Any: ...


@dataclass(frozen=True)
class VerificationResult:
    sources: tuple[str, ...]
    images: dict[str, str]
    runs: dict[str, dict[str, Any]]


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _repository(heads: Mapping[str, str], name: str) -> str:
    value = heads.get(name)
    if not isinstance(value, str) or not value:
        raise VerificationError(f"missing head: {name}")
    return value


def verify_release(
    client: GitHubClient,
    heads: Mapping[str, str],
    builds: Mapping[str, Mapping[str, Any]],
    *,
    repositories: Mapping[str, str] | None = None,
    required_images: Mapping[str, str] | set[str] | tuple[str, ...] = (),
    bootstrap_shas: Mapping[str, set[str] | tuple[str, ...] | list[str]] | None = None,
    owners: set[str] | tuple[str, ...] | Mapping[str, set[str] | tuple[str, ...] | list[str]] = (),
    ruleset_repositories: set[str] | tuple[str, ...] = (),
) -> VerificationResult | str:
    """Verify source heads and already-attested OCI build specifications."""
    repositories = repositories or {name: name for name in heads}
    bootstrap_shas = bootstrap_shas or {}
    for required in ("homelab", "homelab-site"):
        if required not in heads:
            raise VerificationError(f"missing source head: {required}")
    source_names = tuple(sorted(heads))
    sources = []
    verified_repository_heads: dict[str, str] = {}
    for name in source_names:
        sha = _repository(heads, name)
        repo = repositories.get(name, name)
        verified_repository_heads[repo] = sha
        bootstrap = set(bootstrap_shas.get(name, ()))
        if sha in bootstrap:
            sources.append(sha)
            continue
        if name in ruleset_repositories:
            sources.append(sha)
            continue
        # The PR-list API is only an alternate way to find the PR; it must
        # not bypass commit existence and merge validation.
        commit = client.read_commit(repo, sha)
        if commit is None:
            raise VerificationError(f"unknown head: {repo}@{sha}")
        parents = _get(commit, "parents", ())
        if not isinstance(parents, (list, tuple)) or len(parents) < 2:
            raise VerificationError(f"head is not a merge commit: {repo}@{sha}")
        prs = client.pull_requests_for_commit(repo, sha)
        if isinstance(owners, Mapping):
            owner_policy = owners.get(name, owners.get("*", ()))
        else:
            owner_policy = owners
        if not owner_policy and sha not in bootstrap:
            raise VerificationError(f"owner policy is not configured: {name}@{sha}")
        if sha not in bootstrap and not any(
            (
                _get(pr, "merged", _get(pr, "merged_at", False))
                and _get(_get(pr, "base_ref", _get(pr, "base", "")), "ref", _get(pr, "base_ref", _get(pr, "base", ""))) == "main"
                and _get(pr, "merge_commit_sha", _get(pr, "merge_sha")) == sha
                and _get(_get(pr, "merged_by"), "login", _get(pr, "merged_by")) in owner_policy
            )
            for pr in prs
        ):
            raise VerificationError(f"head has no owner-merged PR: {name}@{sha}")
        sources.append(sha)

    wanted = set(required_images) if not isinstance(required_images, Mapping) else set(required_images)
    if not wanted:
        wanted = set(builds)
    missing = wanted - set(builds)
    if missing:
        raise VerificationError(f"missing image: {sorted(missing)[0]}")
    result: dict[str, str] = {}
    selected: dict[str, dict[str, Any]] = {}
    for image in sorted(wanted):
        spec = builds[image]
        repo = spec.get("repository", repositories.get("homelab", "homelab"))
        sha = verified_repository_heads.get(repo)
        if not isinstance(sha, str):
            raise VerificationError(f"build repository has no verified source head: {repo}")
        requested_sha = spec.get("head_sha")
        if requested_sha is not None and requested_sha != sha:
            raise VerificationError(f"build head is not the verified source head: {image}")
        if spec.get("image", image) != image:
            raise VerificationError(f"artifact image does not match: {image}")
        manifest_digest = spec.get("manifest_digest")
        if not isinstance(manifest_digest, str):
            raise VerificationError(f"missing manifest digest for image: {image}")
        digest = spec.get("image_ref")
        if not isinstance(digest, str) or "@sha256:" not in digest:
            registry = spec.get("registry", "ghcr.io/example")
            digest = f"{registry}/{image}@{manifest_digest}"
        result[image] = digest
        selected[image] = dict(spec)
    return VerificationResult(tuple(sources), result, selected)