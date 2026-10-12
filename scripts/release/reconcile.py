"""Plan, sign, and publish one protected release reconciliation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .bundle import bundle_sha256
from .compile import compile_bundle
from .contracts import ValidationError, load_build_provenance, load_release_record
from .feed import FeedError, publish_next
from .github import GitHubClient
from .sign import record_bytes, sign_record
from .verify import VerificationError, VerificationResult, verify_release
from .artifacts import load_approved_destinations

SITE_REPOSITORY = os.environ.get("SITE_REPOSITORY", "jon-robbins/homelab-site")
TRACKER_REPOSITORY = os.environ.get("TRACKER_REPOSITORY", "jon-robbins/tracker-integrations")
BUILD_WORKFLOW = ".github/workflows/post-merge-build.yml"


def _normalize_workflow_runs(value: Any) -> Any:
    """Convert REST repository objects to the string authority used by verify."""
    if not isinstance(value, Mapping):
        return value
    key = "workflow_runs" if "workflow_runs" in value else "runs"
    runs = value.get(key)
    if not isinstance(runs, list):
        return value
    normalized = []
    for run in runs:
        if not isinstance(run, Mapping):
            normalized.append(run)
            continue
        repository_value = run.get("repository")
        if isinstance(repository_value, Mapping):
            repository_value = repository_value.get("full_name")
        normalized.append({**run, "repository": repository_value} if repository_value else dict(run))
    # verify_release accepts the historical ``runs`` spelling, while GitHub's
    # REST response uses ``workflow_runs``.
    return {**value, key: normalized, "runs": normalized}


class _VerificationClient:
    """Keep the production REST shape out of the authority checker."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def workflow_runs(self, repository: str, workflow: str, head_sha: str) -> Any:
        return _normalize_workflow_runs(self._client.workflow_runs(repository, workflow, head_sha))


def _run_builds(client: Any, repository: str, sha: str) -> tuple[int, dict[str, dict[str, Any]]]:
    runs = _normalize_workflow_runs(client.workflow_runs(repository, BUILD_WORKFLOW, sha))
    candidates = runs.get("workflow_runs", runs.get("runs", ()))
    for run in sorted(candidates, key=lambda item: (item.get("created_at", ""), item.get("id", 0)), reverse=True):
        if run.get("head_sha") != sha or run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        run_id = int(run["id"])
        run_attempt = run.get("run_attempt")
        if isinstance(run_attempt, bool) or not isinstance(run_attempt, int) or run_attempt < 1:
            raise VerificationError(f"post-merge build has no valid run attempt: {run_id}")
        run_repository = run.get("repository")
        if isinstance(run_repository, Mapping):
            run_repository = run_repository.get("full_name")
        if run_repository not in (None, repository):
            raise VerificationError(f"post-merge build repository does not match: {run_id}")
        run_workflow = run.get("workflow_ref")
        if run_workflow is None:
            run_workflow = f"{repository}/{BUILD_WORKFLOW}@refs/heads/main"
        images: dict[str, dict[str, Any]] = {}
        artifacts = client.run_artifacts(repository, run_id).get("artifacts", ())
        for artifact in artifacts:
            name = artifact.get("name", "")
            if not name.startswith("oci-"):
                continue
            image_from_name = name[4:]
            if not image_from_name:
                raise VerificationError(f"OCI artifact has no image name: {name}")
            files = client.artifact_files(repository, artifact)
            try:
                provenance = load_build_provenance(files["provenance.json"])
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                raise VerificationError(f"invalid provenance for {name}: {error}") from error
            expected = {
                "repository": repository,
                "source_sha": sha,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "workflow_ref": run_workflow,
                "image": image_from_name,
            }
            for field, expected_value in expected.items():
                if provenance.get(field) != expected_value:
                    raise VerificationError(f"provenance {field} does not match {name}")
            images[image_from_name] = {
                "repository": repository,
                "workflow": BUILD_WORKFLOW,
                "head_sha": sha,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "artifact_name": name,
                "manifest_digest": provenance["manifest_digest"],
                "provenance": provenance,
            }
        return run_id, images
    raise VerificationError(f"no successful post-merge build for {sha}")


def _newest_unpublished_sha(client: Any, repository: str, site_repository: str) -> str | None:
    commits_method = getattr(client, "commits", None)
    commits = commits_method(repository, "main") if callable(commits_method) else []
    if not commits:
        return client.ref(repository, "main")
    published = (client.published_tooling_shas(site_repository, "releases")
                 if hasattr(client, "published_tooling_shas") else set())
    for commit in commits:
        sha = commit.get("sha") if isinstance(commit, Mapping) else None
        if isinstance(sha, str) and sha not in published:
            return sha
    return None


def _materialize_site_inputs(client: Any, repository: str, ref: str, root: Path) -> None:
    """Copy the exact site inputs at *ref* for the signing job artifact."""
    if not hasattr(client, "contents"):
        return
    files = ("compose.json", "manifest.yaml", "probes.json", "policy.yaml")
    for name in files:
        data = client.read(repository, ref, name)
        if data is None:
            raise VerificationError(f"site input is missing at {repository}@{ref}: {name}")
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def copy_directory(path: str) -> None:
        entries = client.contents(repository, ref, path)
        if not isinstance(entries, list):
            raise VerificationError(f"site input directory is not readable: {path}")
        for entry in entries:
            name = entry.get("name")
            kind = entry.get("type")
            if not isinstance(name, str):
                raise VerificationError(f"invalid site input entry in {path}")
            child = f"{path}/{name}" if path else name
            if kind == "dir":
                copy_directory(child)
            elif kind == "file":
                data = client.read(repository, ref, child)
                if data is None:
                    raise VerificationError(f"site input is missing at {repository}@{ref}: {child}")
                target = root / child
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            else:
                raise VerificationError(f"unsupported site input entry: {child}")

    copy_directory("files")


def plan(client: Any | None = None, *, repository: str | None = None, site_repository: str = SITE_REPOSITORY, output: Path | None = None) -> dict[str, Any]:
    client = client or GitHubClient()
    repository = repository or os.environ.get("GITHUB_REPOSITORY", "jon-robbins/homelab")
    sha = _newest_unpublished_sha(client, repository, site_repository)
    previous = client.last_record(site_repository, "releases")
    next_sequence = int(previous["sequence"]) + 1 if previous else 1
    if sha is None:
        result = {"sha": "", "run_id": None, "sequence": next_sequence, "images": []}
    elif previous and previous.get("tooling_git_sha") == sha:
        result = {"sha": sha, "run_id": None, "sequence": next_sequence, "images": []}
    else:
        site_sha = client.ref(site_repository, "main")
        tracker_sha = client.ref(TRACKER_REPOSITORY, "main")
        run_id, builds = _run_builds(client, repository, sha)
        verified = verify_release(_VerificationClient(client), {"homelab": sha, "homelab_site": site_sha, "tracker_integrations": tracker_sha}, builds, repositories={"homelab": repository, "homelab-site": site_repository, "tracker-integrations": TRACKER_REPOSITORY}, owners={"*": set(os.environ.get("RELEASE_OWNERS", "jon-robbins").split(","))})
        if not isinstance(verified, VerificationResult):
            raise VerificationError("build is not complete")
        approved = load_approved_destinations()
        images = [{"image": image, "artifact_name": builds[image]["artifact_name"], "manifest_digest": builds[image]["manifest_digest"], "destination": f"ghcr.io/jon-robbins/homelab/{image}"} for image in sorted(verified.images)]
        if any(item["destination"] not in approved for item in images):
            raise VerificationError("build requested an unapproved destination")
        result = {"sha": sha, "run_id": run_id, "sequence": next_sequence, "images": images, "sources": {"homelab": sha, "homelab_site": site_sha, "tracker_integrations": tracker_sha}}
        input_root = Path(os.environ.get("RELEASE_INPUTS_DIR", "release-inputs"))
        _materialize_site_inputs(client, site_repository, site_sha, input_root)
    if output:
        output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return result


def sign_plan(plan_path: Path, output_dir: Path) -> None:
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan_data.get("images"):
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "bundle.tar"
    input_root = Path(os.environ.get("RELEASE_INPUTS_DIR", "release-inputs"))
    inputs = [input_root / name for name in ("compose.json", "manifest.yaml", "probes.json", "policy.yaml", "files")]
    if not all(path.exists() for path in inputs):
        # Keep the command useful for local operators and older callers that
        # already materialized inputs under the explicit environment paths.
        inputs = [Path(os.environ[name]) for name in ("RELEASE_COMPOSE", "RELEASE_MANIFEST", "RELEASE_PROBES", "RELEASE_POLICY", "RELEASE_FILES")]
    digest = compile_bundle(*inputs, bundle_path)
    record = {"schema": 1, "site_id": "home", "sequence": plan_data["sequence"], "tooling_git_sha": plan_data["sha"], "sources": plan_data["sources"], "images": {item["image"]: item["destination"] + "@" + item["manifest_digest"] for item in plan_data["images"]}, "bundle_sha256": digest, "rollback_floor": 0}
    signing_env = "HOMELAB_RELEASE_SIGNING_KEY" if os.environ.get("HOMELAB_RELEASE_SIGNING_KEY") else "RELEASE_SIGNING_KEY"
    data, signature = sign_record(record, env_var=signing_env)
    (output_dir / "record.json").write_bytes(data)
    (output_dir / "record.sig").write_bytes(signature)


def feed_plan(directory: Path, public_key: Path, *, repository: str = SITE_REPOSITORY, client: Any | None = None) -> str:
    key = public_key.read_bytes()
    if b"PLACEHOLDER" in key:
        raise ValueError("release public key is still a placeholder; commit config/release/release-signing.pub")
    key_id = os.environ.get("RELEASE_SIGNING_KEY_ID", "release-v1")
    key_map_path = public_key.with_name("release-keys.json")
    if key_map_path.exists():
        key_map = json.loads(key_map_path.read_text(encoding="utf-8"))
        key_map = key_map.get("keys", key_map) if isinstance(key_map, dict) else {}
        key_map = {name: key for name, path in key_map.items()
                   if name == key_id and path == public_key.name}
    else:
        key_map = {key_id: key}
    record = load_release_record((directory / "record.json").read_bytes())
    client = client or GitHubClient()
    signature = (directory / "record.sig").read_bytes()
    def pre_signed(next_record: Mapping[str, Any]) -> tuple[bytes, bytes]:
        if dict(next_record) != dict(record):
            raise FeedError("feed sequence changed after signing; reconcile again")
        return record_bytes(record), signature
    return publish_next(client, record, pre_signed, (directory / "bundle.tar").read_bytes(), repository=repository, public_key=key_map)


def write_plan_outputs(value: Mapping[str, Any], output: str | os.PathLike[str]) -> None:
    """Write the plan values exported to later GitHub Actions jobs."""
    with open(output, "a", encoding="utf-8") as stream:
        stream.write(
            f"sha={value['sha']}\n"
            f"run_id={value.get('run_id') or ''}\n"
            f"images={json.dumps(value['images'], separators=(',', ':'))}\n"
            f"has_images={'true' if value['images'] else 'false'}\n"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan"); plan_parser.add_argument("--output", type=Path, default=Path("release-plan.json"))
    sign_parser = sub.add_parser("sign"); sign_parser.add_argument("--plan", type=Path, default=Path("release-plan.json")); sign_parser.add_argument("--output-dir", type=Path, default=Path("release-output"))
    feed_parser = sub.add_parser("feed"); feed_parser.add_argument("--directory", type=Path, default=Path("release-output")); feed_parser.add_argument("--public-key", type=Path, default=Path("config/release/release-signing.pub"))
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            value = plan(output=args.output)
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                write_plan_outputs(value, output)
        elif args.command == "sign": sign_plan(args.plan, args.output_dir)
        else: print(feed_plan(args.directory, args.public_key))
    except (OSError, ValueError, VerificationError) as error:
        print(f"release {args.command} failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
