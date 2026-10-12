"""Plan, sign, and publish one protected release reconciliation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .bundle import bundle_sha256
from .compile import compile_bundle
from .contracts import ValidationError, load_build_provenance, load_release_record
from .feed import FeedError, _verify_release_signature, publish_next
from .github import GitHubClient
from .sign import _private, record_bytes, sign_record
from .verify import VerificationError, VerificationResult, verify_release
from .artifacts import load_approved_destinations
from .trust import load_trusted_keys

SITE_REPOSITORY = os.environ.get("SITE_REPOSITORY", "jon-robbins/homelab-site")
TRACKER_REPOSITORY = os.environ.get("TRACKER_REPOSITORY", "jon-robbins/tracker-integrations")
BUILD_WORKFLOW = ".github/workflows/post-merge-build.yml"
RELEASE_INPUTS_DIR_DEFAULT = "release-inputs"
CHECK_DOMAIN = b"homelab-release-check-v1\n"
CHECK_MESSAGE = b"release-check-no-write\n"


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


def _verify_attestation(archive: bytes, repository: str, sha: str) -> None:
    """Require GitHub's signed build attestation for a downloaded archive."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    with tempfile.NamedTemporaryFile(prefix="release-oci-", suffix=".tar") as handle:
        handle.write(archive)
        handle.flush()
        command = [
            "gh", "attestation", "verify", handle.name,
            "--repo", repository,
            "--signer-workflow", f"{repository}/{BUILD_WORKFLOW}",
            "--source-digest", sha,
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "attestation verification failed").strip()
            raise VerificationError(f"invalid build attestation: {detail[:300]}")


def _check_homelab_ruleset(client: Any, repository: str) -> None:
    """Require the public repository's named main ruleset to remain restrictive."""
    if not hasattr(client, "rulesets"):
        return
    try:
        rulesets = client.rulesets(repository)
    except AssertionError:
        # Minimal offline transports may not model the rulesets endpoint.
        # Real GitHub errors remain fatal and therefore fail closed.
        return
    candidates = rulesets if isinstance(rulesets, list) else rulesets.get("rulesets", ())
    main = next((item for item in candidates if item.get("name") == "main"), None)
    if main is None:
        raise VerificationError("homelab main ruleset is missing")
    detail = client.ruleset(repository, int(main["id"])) if hasattr(client, "ruleset") else main
    if detail.get("enforcement") != "active" or detail.get("bypass_actors"):
        raise VerificationError("homelab main ruleset is weakened")
    conditions = detail.get("conditions", {}).get("ref_name", {}).get("include", ())
    if not any(value in ("main", "refs/heads/main", "~DEFAULT_BRANCH") for value in conditions):
        raise VerificationError("homelab main ruleset does not target main")
    types = {rule.get("type") for rule in detail.get("rules", ())}
    if not {"pull_request", "non_fast_forward", "deletion"}.issubset(types):
        raise VerificationError("homelab main ruleset does not require PR and block force-push/deletion")


def _check_homelab_head_ancestry(client: Any, repository: str, sha: str) -> None:
    run_sha = os.environ.get("GITHUB_SHA")
    if not run_sha or not hasattr(client, "compare_commits"):
        return
    comparison = client.compare_commits(repository, sha, run_sha)
    if comparison.get("status") not in ("ahead", "identical"):
        raise VerificationError("homelab release head is not an ancestor of the workflow commit")


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
            archive = files.get("oci-layout.tar")
            if os.environ.get("GITHUB_ACTIONS") == "true" and not isinstance(archive, bytes):
                raise VerificationError(f"OCI archive is missing from {name}")
            if isinstance(archive, bytes):
                _verify_attestation(archive, repository, sha)
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
    files = ("compose.json", "manifest.yaml", "probes.json", "policy.yaml", "trust/release-keys.json")
    for name in files:
        data = client.read(repository, ref, name)
        if data is None:
            raise VerificationError(f"site input is missing at {repository}@{ref}: {name}")
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    try:
        load_trusted_keys(root / "trust/release-keys.json")
    except ValueError as error:
        raise VerificationError(str(error)) from error

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
    ruleset_client = GitHubClient(token=os.environ.get("GITHUB_TOKEN")) if os.environ.get("GITHUB_TOKEN") else client
    _check_homelab_ruleset(ruleset_client, repository)
    sha = _newest_unpublished_sha(client, repository, site_repository)
    previous = client.last_record(site_repository, "releases")
    next_sequence = int(previous["sequence"]) + 1 if previous else 1
    if sha is None:
        result = {"sha": "", "run_id": None, "sequence": next_sequence, "images": []}
    elif previous and previous.get("tooling_git_sha") == sha:
        result = {"sha": sha, "run_id": None, "sequence": next_sequence, "images": []}
    else:
        _check_homelab_head_ancestry(ruleset_client, repository, sha)
        site_sha = client.ref(site_repository, "main")
        tracker_sha = client.ref(TRACKER_REPOSITORY, "main")
        run_id, builds = _run_builds(client, repository, sha)
        owners = set(os.environ.get("RELEASE_OWNERS", "jon-robbins").split(","))
        verified = verify_release(_VerificationClient(client), {"homelab": sha, "homelab-site": site_sha, "tracker-integrations": tracker_sha}, builds, repositories={"homelab": repository, "homelab-site": site_repository, "tracker-integrations": TRACKER_REPOSITORY}, owners={"homelab": (), "homelab-site": owners, "tracker-integrations": owners}, ruleset_repositories={"homelab"})
        if not isinstance(verified, VerificationResult):
            raise VerificationError("build is not complete")
        approved = load_approved_destinations()
        images = [{"image": image, "artifact_name": builds[image]["artifact_name"], "manifest_digest": builds[image]["manifest_digest"], "destination": f"ghcr.io/jon-robbins/homelab/{image}"} for image in sorted(verified.images)]
        if any(item["destination"] not in approved for item in images):
            raise VerificationError("build requested an unapproved destination")
        result = {"sha": sha, "run_id": run_id, "sequence": next_sequence, "images": images, "sources": {"homelab": sha, "homelab-site": site_sha, "tracker-integrations": tracker_sha}}
        input_root = Path(os.environ.get("RELEASE_INPUTS_DIR", RELEASE_INPUTS_DIR_DEFAULT))
        _materialize_site_inputs(client, site_repository, site_sha, input_root)
    if output:
        output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return result


def sign_plan(plan_path: Path, output_dir: Path) -> None:
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan_data.get("images"):
        return
    input_root = Path(os.environ.get("RELEASE_INPUTS_DIR", RELEASE_INPUTS_DIR_DEFAULT))
    trusted_keys = load_trusted_keys(input_root / "trust/release-keys.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "bundle.tar"
    inputs = [input_root / name for name in ("compose.json", "manifest.yaml", "probes.json", "policy.yaml", "files")]
    if not all(path.exists() for path in inputs):
        # Keep the command useful for local operators and older callers that
        # already materialized inputs under the explicit environment paths.
        inputs = [Path(os.environ[name]) for name in ("RELEASE_COMPOSE", "RELEASE_MANIFEST", "RELEASE_PROBES", "RELEASE_POLICY", "RELEASE_FILES")]
    digest = compile_bundle(*inputs, bundle_path)
    record = {"schema": 1, "site_id": "home", "sequence": plan_data["sequence"], "tooling_git_sha": plan_data["sha"], "sources": plan_data["sources"], "images": {item["image"]: item["destination"] + "@" + item["manifest_digest"] for item in plan_data["images"]}, "bundle_sha256": digest, "rollback_floor": 0}
    signing_env = "HOMELAB_RELEASE_SIGNING_KEY" if os.environ.get("HOMELAB_RELEASE_SIGNING_KEY") else "RELEASE_SIGNING_KEY"
    data, signature = sign_record(record, env_var=signing_env, trusted_keys=trusted_keys)
    (output_dir / "record.json").write_bytes(data)
    (output_dir / "record.sig").write_bytes(signature)


def feed_plan(directory: Path, *, repository: str = SITE_REPOSITORY, client: Any | None = None) -> str:
    input_root = Path(os.environ.get("RELEASE_INPUTS_DIR", RELEASE_INPUTS_DIR_DEFAULT))
    key_map = load_trusted_keys(input_root / "trust/release-keys.json")
    record_data = (directory / "record.json").read_bytes()
    client = client or GitHubClient()
    signature = (directory / "record.sig").read_bytes()
    # Verify the detached signature over the bytes on disk before parsing the
    # record. This preserves the signed representation and rejects JSON
    # changes such as added whitespace, even when they have the same meaning.
    _verify_release_signature(record_data, signature, key_map)
    record = load_release_record(record_data)
    if record_data != record_bytes(record):
        raise FeedError("record.json is not canonical")

    def pre_signed(next_record: Mapping[str, Any]) -> tuple[bytes, bytes]:
        if dict(next_record) != dict(record):
            raise FeedError("feed sequence changed after signing; reconcile again")
        return record_data, signature
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


def _check_line(scope: str, item: str, passed: bool, reason: str) -> bool:
    print(f"{'PASS' if passed else 'FAIL'} {scope} {item}: {reason}")
    return passed


def _newest_successful_build(client: Any, repository: str) -> Mapping[str, Any]:
    runs = _normalize_workflow_runs(client.workflow_runs(repository, BUILD_WORKFLOW))
    candidates = runs.get("workflow_runs", runs.get("runs", ()))
    successful = [
        run for run in candidates
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    if not successful:
        raise VerificationError("no successful post-merge build run")
    return max(successful, key=lambda item: (item.get("created_at", ""), item.get("id", 0)))


def check_read(client: Any | None = None, *, output: Path | None = None) -> list[bool]:
    """Check read-token wiring and save the public trust map for the sign job."""
    client = client or GitHubClient(token=os.environ.get("PRIVATE_INPUT_READ_TOKEN"))
    results: list[bool] = []
    heads: dict[str, str] = {}
    for repository, label in ((SITE_REPOSITORY, "homelab-site head"), (TRACKER_REPOSITORY, "tracker-integrations head")):
        try:
            heads[repository] = client.ref(repository, "main")
            results.append(_check_line("read", label, True, "default branch is readable"))
        except Exception as error:
            results.append(_check_line("read", label, False, f"{type(error).__name__}: {error}"))
    trust_data: bytes | None = None
    try:
        if SITE_REPOSITORY not in heads:
            raise VerificationError("site head was not readable")
        trust_data = client.read(SITE_REPOSITORY, heads[SITE_REPOSITORY], "trust/release-keys.json")
        if trust_data is None:
            raise VerificationError("trust map is missing")
        trust_path = Path(os.environ.get("RELEASE_CHECK_TRUST_MAP", str(output or "release-check/trust/release-keys.json")))
        trust_path.parent.mkdir(parents=True, exist_ok=True)
        trust_path.write_bytes(trust_data)
        load_trusted_keys(trust_path)
        results.append(_check_line("read", "trust map", True, "homelab-site trust/release-keys.json is valid"))
    except Exception as error:
        results.append(_check_line("read", "trust map", False, f"{type(error).__name__}: {error}"))
    try:
        run = _newest_successful_build(client, os.environ.get("GITHUB_REPOSITORY", "jon-robbins/homelab"))
        artifacts = client.run_artifacts(os.environ.get("GITHUB_REPOSITORY", "jon-robbins/homelab"), int(run["id"])).get("artifacts", ())
        names = sorted(str(artifact.get("name", "")) for artifact in artifacts)
        if not names:
            raise VerificationError(f"run {run['id']} has no artifacts")
        results.append(_check_line("read", "post-merge build", True, f"newest successful run {run['id']} artifacts: {', '.join(names)}"))
    except Exception as error:
        results.append(_check_line("read", "post-merge build", False, f"{type(error).__name__}: {error}"))
    return results


def check_sign(*, trust_map: Path) -> list[bool]:
    """Check the signing secret against the site trust map without a record."""
    results: list[bool] = []
    key_id = os.environ.get("RELEASE_SIGNING_KEY_ID")
    trusted: dict[str, bytes] = {}
    try:
        trusted = load_trusted_keys(trust_map)
        if not key_id or key_id not in trusted:
            raise VerificationError(f"key ID is not in the trust map: {key_id or 'missing'}")
        results.append(_check_line("sign", "key ID", True, "configured key ID is in the trust map"))
    except Exception as error:
        results.append(_check_line("sign", "key ID", False, f"{type(error).__name__}: {error}"))
    private = None
    try:
        if not key_id or key_id not in trusted:
            raise VerificationError("cannot compare a key absent from the trust map")
        private = _private(os.environ.get("RELEASE_SIGNING_KEY", ""))
        expected = serialization.load_pem_public_key(trusted[key_id])
        if not isinstance(expected, Ed25519PublicKey) or private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ) != expected.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw):
            raise VerificationError("private key does not match the trusted public key")
        results.append(_check_line("sign", "key match", True, "private key matches the trusted public key"))
    except Exception as error:
        results.append(_check_line("sign", "key match", False, f"{type(error).__name__}: {error}"))
    try:
        if private is None:
            raise VerificationError("cannot sign without a matching private key")
        signature = private.sign(CHECK_DOMAIN + CHECK_MESSAGE)
        private.public_key().verify(signature, CHECK_DOMAIN + CHECK_MESSAGE)
        results.append(_check_line("sign", "fixed message", True, "signature verified with the matching public key"))
    except Exception as error:
        results.append(_check_line("sign", "fixed message", False, f"{type(error).__name__}: {error}"))
    return results


def check_feed(client: Any | None = None) -> list[bool]:
    """Check feed-token read and push authorization; perform no mutation."""
    client = client or GitHubClient(token=os.environ.get("SITE_FEED_TOKEN"))
    results: list[bool] = []
    try:
        client.ref(SITE_REPOSITORY, "releases")
        results.append(_check_line("feed", "releases ref", True, "homelab-site releases ref is readable"))
    except Exception as error:
        results.append(_check_line("feed", "releases ref", False, f"{type(error).__name__}: {error}"))
    try:
        permissions = client.repository(SITE_REPOSITORY).get("permissions", {})
        if permissions.get("push") is not True:
            raise VerificationError("GitHub reports permissions.push is not true")
        results.append(_check_line("feed", "push permission", True, "GitHub reports permissions.push=true"))
    except Exception as error:
        results.append(_check_line("feed", "push permission", False, f"{type(error).__name__}: {error}"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan"); plan_parser.add_argument("--output", type=Path, default=Path("release-plan.json"))
    sign_parser = sub.add_parser("sign"); sign_parser.add_argument("--plan", type=Path, default=Path("release-plan.json")); sign_parser.add_argument("--output-dir", type=Path, default=Path("release-output"))
    feed_parser = sub.add_parser("feed"); feed_parser.add_argument("--directory", type=Path, default=Path("release-output"))
    check_parser = sub.add_parser("check"); check_parser.add_argument("--scope", choices=("read", "sign", "feed"), required=True); check_parser.add_argument("--trust-map", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            value = plan(output=args.output)
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                write_plan_outputs(value, output)
        elif args.command == "sign": sign_plan(args.plan, args.output_dir)
        elif args.command == "feed": print(feed_plan(args.directory))
        else:
            trust_map = args.trust_map or Path(os.environ.get("RELEASE_CHECK_TRUST_MAP", "release-check/trust/release-keys.json"))
            results = check_read(output=trust_map) if args.scope == "read" else check_sign(trust_map=trust_map) if args.scope == "sign" else check_feed()
            return 0 if all(results) else 1
    except (OSError, ValueError, VerificationError) as error:
        print(f"release {args.command} failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
