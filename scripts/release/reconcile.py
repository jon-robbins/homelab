"""Plan, sign, and publish one protected release reconciliation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .bundle import bundle_sha256
from .compile import compile_bundle
from .contracts import load_release_record
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
        images: dict[str, dict[str, Any]] = {}
        artifacts = client.run_artifacts(repository, int(run["id"])).get("artifacts", ())
        for artifact in artifacts:
            name = artifact.get("name", "")
            if not name.startswith("oci-"):
                continue
            files = client.artifact_files(repository, artifact)
            provenance = json.loads(files.get("provenance.json", b"{}"))
            image = provenance.get("image")
            digest = provenance.get("manifest_digest")
            if isinstance(image, str) and isinstance(digest, str):
                images[image] = {"repository": repository, "workflow": BUILD_WORKFLOW, "head_sha": sha, "run_id": int(run["id"]), "run_attempt": int(run.get("run_attempt", 1)), "image": image, "manifest_digest": digest, "artifact_name": name}
        return int(run["id"]), images
    raise VerificationError(f"no successful post-merge build for {sha}")


def plan(client: Any | None = None, *, repository: str | None = None, site_repository: str = SITE_REPOSITORY, output: Path | None = None) -> dict[str, Any]:
    client = client or GitHubClient()
    repository = repository or os.environ.get("GITHUB_REPOSITORY", "jon-robbins/homelab")
    sha = client.ref(repository, "main")
    previous = client.last_record(site_repository, "releases")
    next_sequence = int(previous["sequence"]) + 1 if previous else 1
    if previous and previous.get("tooling_git_sha") == sha:
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
    if output:
        output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return result


def sign_plan(plan_path: Path, output_dir: Path) -> None:
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan_data.get("images"):
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "bundle.tar"
    # The checked-out tooling supplies the same inputs used by the release gate.
    inputs = [Path(os.environ[name]) for name in ("RELEASE_COMPOSE", "RELEASE_MANIFEST", "RELEASE_PROBES", "RELEASE_POLICY", "RELEASE_FILES")]
    digest = compile_bundle(*inputs, bundle_path)
    record = {"schema": 1, "site_id": "home", "sequence": plan_data["sequence"], "tooling_git_sha": plan_data["sha"], "sources": plan_data["sources"], "images": {item["image"]: item["destination"] + "@" + item["manifest_digest"] for item in plan_data["images"]}, "bundle_sha256": digest, "rollback_floor": 0}
    data, signature = sign_record(record, env_var="RELEASE_SIGNING_KEY")
    (output_dir / "record.json").write_bytes(data)
    (output_dir / "record.sig").write_bytes(signature)


def feed_plan(directory: Path, public_key: Path, *, repository: str = SITE_REPOSITORY, client: Any | None = None) -> str:
    key = public_key.read_bytes()
    if b"PLACEHOLDER" in key:
        raise ValueError("release public key is still a placeholder; commit config/release/release-signing.pub")
    record = load_release_record((directory / "record.json").read_bytes())
    client = client or GitHubClient()
    signature = (directory / "record.sig").read_bytes()
    def pre_signed(next_record: Mapping[str, Any]) -> tuple[bytes, bytes]:
        if dict(next_record) != dict(record):
            raise FeedError("feed sequence changed after signing; reconcile again")
        return record_bytes(record), signature
    return publish_next(client, record, pre_signed, (directory / "bundle.tar").read_bytes(), repository=repository, public_key=key)


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
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(f"sha={value['sha']}\nrun_id={value.get('run_id') or ''}\nimages={json.dumps(value['images'], separators=(',', ':'))}\nhas_images={'true' if value['images'] else 'false'}\n")
        elif args.command == "sign": sign_plan(args.plan, args.output_dir)
        else: print(feed_plan(args.directory, args.public_key))
    except (OSError, ValueError, VerificationError) as error:
        print(f"release {args.command} failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
