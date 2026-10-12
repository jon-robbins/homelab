"""Offline-testable authority checks for release inputs.

The client is deliberately tiny: callers provide an object with methods for
``commit``, ``pull_request`` and ``build_runs``.  The methods may return
dicts or objects with the same attribute names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class VerificationError(ValueError):
    """A release input is not authoritative."""


class GitHubClient(Protocol):
    def commit(self, repository: str, sha: str) -> Any: ...
    def pull_request(self, repository: str, sha: str) -> Any | None: ...
    def build_runs(self, repository: str, workflow: str, head_sha: str) -> Any: ...


@dataclass(frozen=True)
class VerificationResult:
    sources: tuple[str, ...]
    images: dict[str, str]
    runs: dict[str, dict[str, Any]]


ReleaseDenied = VerificationError
Verification = VerificationResult


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _repository(heads: Mapping[str, str], name: str) -> str:
    value = heads.get(name)
    if not isinstance(value, str) or not value:
        raise VerificationError(f"missing head: {name}")
    return value


def _check_head(client: GitHubClient, repository: str, sha: str, bootstrap: set[str], owners: set[str]) -> None:
    if sha in bootstrap:
        return
    commit = client.commit(repository, sha)
    if commit is None:
        raise VerificationError(f"unknown head: {repository}@{sha}")
    parents = _get(commit, "parents", ())
    if not isinstance(parents, (list, tuple)) or len(parents) < 2:
        raise VerificationError(f"head is not a merge commit: {repository}@{sha}")
    pull = client.pull_request(repository, sha)
    if pull is None or _get(pull, "merged", False) is not True:
        raise VerificationError(f"head has no owner-merged PR: {repository}@{sha}")
    if _get(pull, "base", _get(pull, "base_ref")) not in ("main", "refs/heads/main"):
        raise VerificationError(f"PR was not merged into main: {repository}@{sha}")
    merged_by = _get(pull, "merged_by", _get(pull, "author"))
    login = _get(merged_by, "login", merged_by)
    if not owners:
        raise VerificationError(f"owner policy is not configured: {repository}@{sha}")
    if login not in owners:
        raise VerificationError(f"PR was not merged by an owner: {repository}@{sha}")
    merge_commit_sha = _get(pull, "merge_commit_sha", _get(pull, "merge_sha"))
    if merge_commit_sha != sha:
        raise VerificationError(f"PR merge commit does not match head: {repository}@{sha}")


def _successful_run(runs: Any, image: str, manifest_digest: str, repository: str, workflow: str, sha: str, expected_attempt: int | None, expected_run_id: int | None, expected_caller: str | None) -> dict[str, Any] | None:
    if isinstance(runs, Mapping):
        runs = runs.get("runs", ())
    for run in runs or ():
        actual_workflow = _get(run, "workflow", _get(run, "workflow_path", _get(run, "path", workflow)))
        if (_get(run, "status") == "completed" and _get(run, "conclusion") == "success"
                and _get(run, "head_sha") == sha and _get(run, "repository", repository) == repository
                and actual_workflow == workflow):
            attempt = _get(run, "run_attempt", _get(run, "attempt"))
            if expected_attempt is not None and attempt != expected_attempt:
                continue
            if expected_run_id is not None and _get(run, "id", _get(run, "run_id")) != expected_run_id:
                continue
            caller = _get(run, "caller", _get(run, "actor", _get(run, "triggering_actor")))
            if expected_caller is not None and _get(caller, "login", caller) != expected_caller:
                continue
            provenance = _get(run, "provenance", run)
            if (_get(provenance, "image") != image
                    or _get(provenance, "manifest_digest") != manifest_digest):
                continue
            data = dict(run) if isinstance(run, Mapping) else {key: getattr(run, key) for key in vars(run)}
            data.setdefault("image", image)
            return data
    return None


def verify_release(
    client: GitHubClient,
    heads: Mapping[str, str],
    builds: Mapping[str, Mapping[str, Any]],
    *,
    repositories: Mapping[str, str] | None = None,
    required_images: Mapping[str, str] | set[str] | tuple[str, ...] = (),
    bootstrap_shas: Mapping[str, set[str] | tuple[str, ...] | list[str]] | None = None,
    owners: set[str] | tuple[str, ...] | Mapping[str, set[str] | tuple[str, ...] | list[str]] = (),
    workflow: str = ".github/workflows/post-merge-build.yml",
    expected_attempts: Mapping[str, int] | None = None,
    expected_caller: str | None = None,
) -> VerificationResult | str:
    """Verify heads and their exact successful post-merge build runs.

    ``"pending"`` is returned when a matching run exists but is incomplete.
    ``required_images`` may map image names to immutable image references; when
    it is a collection, the build provenance must provide ``image_ref``.
    """
    repositories = repositories or {name: name for name in heads}
    bootstrap_shas = bootstrap_shas or {}
    # The public source names follow repository names; accept underscore aliases
    # because they are the keys used by ReleaseRecord.sources.
    aliases = {"homelab": "homelab", "homelab-site": "homelab_site", "tracker-integrations": "tracker_integrations"}
    source_names = ("homelab", "homelab-site", "tracker-integrations")
    sources = []
    verified_repository_heads: dict[str, str] = {}
    for name in source_names:
        head_key = name if name in heads else aliases[name]
        if head_key not in heads:
            # Legacy ReleaseRecord callers omitted the optional tracker source.
            if name == "tracker-integrations" and "homelab_site" in heads:
                continue
            raise VerificationError(f"missing source head: {name}")
        sha = _repository(heads, head_key)
        repo = repositories.get(name, repositories.get(head_key, name))
        verified_repository_heads[repo] = sha
        bootstrap = set(bootstrap_shas.get(name, bootstrap_shas.get(head_key, ())))
        if sha in bootstrap:
            sources.append(sha)
            continue
        # The PR-list API is only an alternate way to find the PR; it must
        # not bypass commit existence and merge validation.
        commit = client.commit(repo, sha)
        if commit is None:
            raise VerificationError(f"unknown head: {repo}@{sha}")
        parents = _get(commit, "parents", ())
        if not isinstance(parents, (list, tuple)) or len(parents) < 2:
            raise VerificationError(f"head is not a merge commit: {repo}@{sha}")
        if hasattr(client, "pull_requests_for_commit"):
            prs = client.pull_requests_for_commit(repo, sha)
            if isinstance(owners, Mapping):
                owner_policy = owners.get(name, owners.get(head_key, owners.get("*", ())))
            else:
                owner_policy = owners
            if not owner_policy and sha not in set(bootstrap_shas.get(name, bootstrap_shas.get(head_key, ()))):
                raise VerificationError(f"owner policy is not configured: {name}@{sha}")
            if not any(_get(pr, "merged", _get(pr, "merged_at", False))
                       and _get(_get(pr, "base_ref", _get(pr, "base", "")), "ref", _get(pr, "base_ref", _get(pr, "base", ""))) == "main"
                       and _get(pr, "merge_commit_sha", _get(pr, "merge_sha")) == sha
                       and _get(_get(pr, "merged_by"), "login", _get(pr, "merged_by")) in owner_policy
                       for pr in prs) and sha not in set(bootstrap_shas.get(name, bootstrap_shas.get(head_key, ()))):
                raise VerificationError(f"head has no owner-merged PR: {name}@{sha}")
        else:
            owner_policy = owners.get(name, owners.get(head_key, owners.get("*", ()))) if isinstance(owners, Mapping) else owners
            _check_head(client, repo, sha, set(bootstrap_shas.get(name, bootstrap_shas.get(head_key, ()))), set(owner_policy))
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
        workflow_name = spec.get("workflow_path", spec.get("workflow", workflow))
        provenance = spec.get("provenance", spec)
        image_ref = provenance.get("image_ref")
        if isinstance(image_ref, str) and "@" in image_ref:
            manifest_digest = image_ref.rsplit("@", 1)[1]
        else:
            manifest_digest = provenance.get("manifest_digest")
        if not isinstance(manifest_digest, str):
            raise VerificationError(f"missing manifest digest for image: {image}")
        if hasattr(client, "workflow_runs"):
            runs = client.workflow_runs(repo, workflow_name, sha)
        else:
            runs = client.build_runs(repo, workflow_name, sha)
        selected_run = _successful_run(
            runs, image, manifest_digest, repo, workflow_name, sha,
            (expected_attempts or {}).get(image, spec.get("run_attempt")),
            spec.get("run_id"), spec.get("caller", expected_caller),
        )
        if selected_run is None:
            candidates = runs.get("runs", ()) if isinstance(runs, Mapping) else runs or ()
            if any(_get(run, "status") not in ("completed", None)
                   and _get(run, "head_sha") == sha
                   and _get(run, "repository", repo) == repo
                   and _get(run, "workflow", _get(run, "workflow_path", _get(run, "path", workflow_name))) == workflow_name
                   for run in candidates):
                return "pending"
            raise VerificationError(f"no exact successful build for image: {image}")
        digest = provenance.get("image_ref", provenance.get("manifest_digest"))
        if not isinstance(digest, str) or "@sha256:" not in digest:
            registry = provenance.get("registry", "ghcr.io/example")
            digest = f"{registry}/{image}@{provenance.get('manifest_digest', '')}"
        result[image] = digest
        selected[image] = selected_run
    return VerificationResult(tuple(sources), result, selected)


def verify_heads(client: GitHubClient, heads: Mapping[str, str], builds: Mapping[str, Mapping[str, Any]], **kwargs: Any) -> VerificationResult | str:
    """Compatibility entry point using the injected client's original API."""
    return verify_release(client, heads, builds, **kwargs)


verify = verify_release
