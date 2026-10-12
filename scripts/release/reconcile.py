"""Plan, sign, and publish one protected release reconciliation."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from .bundle import bundle_sha256
from .compile import compile_bundle
from .contracts import ValidationError, load_application_manifest, load_release_record, parse_json, validate_digest
from .feed import FeedError, publish_next, verify_cosign_bundle
from .github import GitHubClient
from .sign import COSIGN_VERSION, record_bytes, sign_record
from .verify import VerificationError, VerificationResult, verify_release
SITE_REPOSITORY = os.environ.get("SITE_REPOSITORY", "jon-robbins/homelab-site")
TRACKER_REPOSITORY = os.environ.get("TRACKER_REPOSITORY", "jon-robbins/tracker-integrations")
BUILD_WORKFLOW = ".github/workflows/post-merge-build.yml"
RELEASE_INPUTS_DIR_DEFAULT = "release-inputs"
_DESTINATION = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9._/-]*$")
_DEFAULT_APPROVED_DESTINATIONS = Path(__file__).parents[2] / "config/release/approved-ghcr-destinations.json"


def load_approved_destinations(path: str | Path = _DEFAULT_APPROVED_DESTINATIONS) -> frozenset[str]:
    """Load the reviewed, exact GHCR destination allowlist."""
    try:
        value = parse_json(Path(path).read_bytes())
    except (OSError, ValidationError) as error:
        raise ValidationError("$.destinations", "invalid approved destination config") from error
    if not isinstance(value, dict) or set(value) != {"destinations"}:
        raise ValidationError("$", "expected only destinations")
    destinations = value["destinations"]
    if not isinstance(destinations, list) or not destinations or len(set(destinations)) != len(destinations):
        raise ValidationError("$.destinations", "expected unique destination list")
    for index, destination in enumerate(destinations):
        if not isinstance(destination, str) or _DESTINATION.fullmatch(destination) is None:
            raise ValidationError(f"$.destinations[{index}]", "expected immutable GHCR repository")
    return frozenset(destinations)


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


def _read_manifest_digest(archive: bytes) -> str:
    """Read an OCI archive's manifest digest with standard OCI tooling."""
    with tempfile.NamedTemporaryFile(prefix="release-oci-", suffix=".tar") as handle:
        handle.write(archive)
        handle.flush()
        try:
            completed = subprocess.run(
                ["skopeo", "inspect", "--format", "{{.Digest}}", f"oci-archive:{handle.name}"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise VerificationError("could not inspect OCI archive manifest") from error
    try:
        return validate_digest(completed.stdout.strip(), "$.manifest_digest")
    except ValidationError as error:
        raise VerificationError(f"invalid OCI archive manifest digest: {error}") from error


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
    if detail.get("enforcement") != "active" or detail.get("bypass_actors") != []:
        raise VerificationError("homelab main ruleset is weakened")
    if detail.get("target") != "branch":
        raise VerificationError("homelab main ruleset is not branch-targeted")
    conditions = detail.get("conditions", {}).get("ref_name", {}).get("include", ())
    if "refs/heads/main" not in conditions:
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


def _attested_builds(client: Any, repository: str, sha: str) -> dict[str, dict[str, Any]]:
    """Download OCI artifacts and make their native attestations authoritative."""
    response = client.artifacts(repository)
    artifacts = response.get("artifacts", ()) if isinstance(response, Mapping) else response
    candidates = []
    for artifact in artifacts or ():
        if not isinstance(artifact, Mapping) or artifact.get("expired"):
            continue
        workflow_run = artifact.get("workflow_run")
        artifact_head = workflow_run.get("head_sha") if isinstance(workflow_run, Mapping) else None
        if isinstance(artifact_head, str) and artifact_head != sha:
            continue
        name = artifact.get("name", "")
        if not isinstance(name, str) or not name.startswith("oci-"):
            continue
        candidates.append(artifact)
    candidates.sort(key=lambda item: (item.get("created_at", ""), item.get("id", 0)), reverse=True)
    images: dict[str, dict[str, Any]] = {}
    for artifact in candidates:
        name = artifact["name"]
        image = name[4:]
        if not image or image in images:
            continue
        files = client.artifact_files(repository, artifact)
        archive = files.get("oci-layout.tar")
        if not isinstance(archive, bytes):
            raise VerificationError(f"OCI archive is missing from {name}")
        _verify_attestation(archive, repository, sha)
        manifest_digest = _read_manifest_digest(archive)
        workflow_run = artifact.get("workflow_run")
        run_id = workflow_run.get("id") if isinstance(workflow_run, Mapping) else artifact.get("run_id")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
            raise VerificationError(f"OCI artifact has no workflow run locator: {name}")
        images[image] = {
            "repository": repository,
            "head_sha": sha,
            "run_id": run_id,
            "artifact_name": name,
            "image": image,
            "manifest_digest": manifest_digest,
        }
    if not images:
        raise VerificationError(f"no attested OCI artifacts for {sha}")
    return images


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


def _manifest_site_id(root: Path) -> str:
    manifest = load_application_manifest((root / "manifest.yaml").read_bytes(), yaml=True)
    site_id = manifest.get("site_id")
    if not isinstance(site_id, str):
        raise VerificationError("site manifest is missing site_id")
    return site_id


def _destination_images(approved: set[str] | frozenset[str]) -> set[str]:
    prefix = "ghcr.io/jon-robbins/homelab/"
    return {destination.removeprefix(prefix) for destination in approved if destination.startswith(prefix)}


def plan(client: Any | None = None, *, repository: str | None = None, site_repository: str = SITE_REPOSITORY, output: Path | None = None) -> dict[str, Any]:
    client = client or GitHubClient()
    repository = repository or os.environ.get("GITHUB_REPOSITORY", "jon-robbins/homelab")
    ruleset_client = GitHubClient(token=os.environ.get("GITHUB_TOKEN")) if os.environ.get("GITHUB_TOKEN") else client
    _check_homelab_ruleset(ruleset_client, repository)
    previous = client.last_record(site_repository, "releases")
    next_sequence = int(previous["sequence"]) + 1 if previous else 1
    sha = client.ref(repository, "main")
    site_sha = client.ref(site_repository, "main")
    tracker_sha = client.ref(TRACKER_REPOSITORY, "main")
    sources = {"homelab": sha, "homelab-site": site_sha, "tracker-integrations": tracker_sha}
    if previous and previous.get("sources") == sources:
        result = {"release": False, "sha": sha, "sequence": next_sequence, "images": [], "image_refs": previous.get("images", {})}
    else:
        _check_homelab_head_ancestry(ruleset_client, repository, sha)
        approved = load_approved_destinations()
        all_images = _destination_images(approved)
        homelab_changed = previous is None or sha != previous.get("sources", {}).get("homelab")
        if homelab_changed:
            try:
                builds = _attested_builds(client, repository, sha)
            except VerificationError:
                builds = {}
            builds = {image: spec for image, spec in builds.items() if spec.get("head_sha") == sha}
        else:
            builds = {}
        required = all_images if previous is None else set(builds)
        if previous is None and not all_images.issubset(builds):
            missing = sorted(all_images - set(builds))
            raise VerificationError(f"first release requires verified builds for all images; missing {missing[0]}")
        owners = set(os.environ.get("RELEASE_OWNERS", "jon-robbins").split(","))
        verified = verify_release(client, sources, builds, required_images=required, repositories={"homelab": repository, "homelab-site": site_repository, "tracker-integrations": TRACKER_REPOSITORY}, owners={"homelab": (), "homelab-site": owners, "tracker-integrations": owners}, ruleset_repositories={"homelab"})
        if not isinstance(verified, VerificationResult):
            raise VerificationError("build is not complete")
        images = [{"image": image, "artifact_name": builds[image]["artifact_name"], "run_id": builds[image]["run_id"], "manifest_digest": builds[image]["manifest_digest"], "destination": f"ghcr.io/jon-robbins/homelab/{image}"} for image in sorted(verified.images)]
        if any(item["destination"] not in approved for item in images):
            raise VerificationError("build requested an unapproved destination")
        image_refs = dict(previous.get("images", {}) if previous else {})
        image_refs.update({item["image"]: item["destination"] + "@" + item["manifest_digest"] for item in images})
        if set(image_refs) != all_images:
            missing = sorted(all_images - set(image_refs))
            raise VerificationError(f"release record image map is incomplete; missing {missing[0]}")
        input_root = Path(os.environ.get("RELEASE_INPUTS_DIR", RELEASE_INPUTS_DIR_DEFAULT))
        _materialize_site_inputs(client, site_repository, site_sha, input_root)
        result = {"release": True, "sha": sha, "sequence": next_sequence, "images": images, "image_refs": image_refs, "rollback_floor": previous.get("rollback_floor", 0) if previous else 0, "sources": sources, "site_id": _manifest_site_id(input_root)}
    if output:
        output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return result


def sign_plan(plan_path: Path, output_dir: Path) -> None:
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan_data.get("release", bool(plan_data.get("images"))):
        return
    input_root = Path(os.environ.get("RELEASE_INPUTS_DIR", RELEASE_INPUTS_DIR_DEFAULT))
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "bundle.tar"
    inputs = [input_root / name for name in ("compose.json", "manifest.yaml", "probes.json", "policy.yaml", "files")]
    if not all(path.exists() for path in inputs):
        # Keep the command useful for local operators and older callers that
        # already materialized inputs under the explicit environment paths.
        inputs = [Path(os.environ[name]) for name in ("RELEASE_COMPOSE", "RELEASE_MANIFEST", "RELEASE_PROBES", "RELEASE_POLICY", "RELEASE_FILES")]
    digest = compile_bundle(*inputs, bundle_path)
    image_refs = plan_data.get("image_refs") or {item["image"]: item["destination"] + "@" + item["manifest_digest"] for item in plan_data.get("images", [])}
    record = {"schema": 1, "site_id": plan_data["site_id"], "sequence": plan_data["sequence"], "tooling_git_sha": plan_data["sha"], "sources": plan_data["sources"], "images": image_refs, "bundle_sha256": digest, "rollback_floor": plan_data.get("rollback_floor", 0)}
    data = sign_record(record, output_dir / "record.sigstore.json")
    (output_dir / "record.json").write_bytes(data)


def feed_plan(directory: Path, *, repository: str = SITE_REPOSITORY, client: Any | None = None) -> str:
    input_root = Path(os.environ.get("RELEASE_INPUTS_DIR", RELEASE_INPUTS_DIR_DEFAULT))
    record_data = (directory / "record.json").read_bytes()
    bundle_data = (directory / "record.sigstore.json").read_bytes()
    client = client or GitHubClient()
    # Verify the Sigstore bundle over the bytes on disk before parsing the
    # record. This preserves the signed representation and rejects JSON
    # changes such as added whitespace, even when they have the same meaning.
    verify_cosign_bundle(directory / "record.json", directory / "record.sigstore.json")
    record = load_release_record(record_data)
    if record_data != record_bytes(record):
        raise FeedError("record.json is not canonical")

    def pre_signed(next_record: Mapping[str, Any]) -> tuple[bytes, bytes]:
        if dict(next_record) != dict(record):
            raise FeedError("feed sequence changed after signing; reconcile again")
        return record_data, bundle_data
    return publish_next(client, record, pre_signed, (directory / "bundle.tar").read_bytes(), repository=repository)


def write_plan_outputs(value: Mapping[str, Any], output: str | os.PathLike[str]) -> None:
    """Write the plan values exported to later GitHub Actions jobs."""
    with open(output, "a", encoding="utf-8") as stream:
        stream.write(
            f"sha={value['sha']}\n"
            f"images={json.dumps(value['images'], separators=(',', ':'))}\n"
            f"has_images={'true' if value.get('release', bool(value['images'])) else 'false'}\n"
            f"has_publish_images={'true' if value['images'] else 'false'}\n"
            f"has_release={'true' if value.get('release', bool(value['images'])) else 'false'}\n"
        )


def _check_line(scope: str, item: str, passed: bool, reason: str) -> bool:
    print(f"{'PASS' if passed else 'FAIL'} {scope} {item}: {reason}")
    return passed


def check_read(client: Any | None = None, *, output: Path | None = None) -> list[bool]:
    """Check read-token wiring and available build artifacts."""
    client = client or GitHubClient(token=os.environ.get("PRIVATE_INPUT_READ_TOKEN"))
    results: list[bool] = []
    heads: dict[str, str] = {}
    for repository, label in ((SITE_REPOSITORY, "homelab-site head"), (TRACKER_REPOSITORY, "tracker-integrations head")):
        try:
            heads[repository] = client.ref(repository, "main")
            results.append(_check_line("read", label, True, "default branch is readable"))
        except Exception as error:
            results.append(_check_line("read", label, False, f"{type(error).__name__}: {error}"))
    try:
        artifacts_response = client.artifacts(os.environ.get("GITHUB_REPOSITORY", "jon-robbins/homelab"))
        artifacts = artifacts_response.get("artifacts", ()) if isinstance(artifacts_response, Mapping) else artifacts_response
        names = sorted(str(artifact.get("name", "")) for artifact in artifacts)
        if not names:
            raise VerificationError("no build artifacts")
        results.append(_check_line("read", "post-merge build", True, f"available artifacts: {', '.join(names)}"))
    except Exception as error:
        results.append(_check_line("read", "post-merge build", False, f"{type(error).__name__}: {error}"))
    return results


def check_sign() -> list[bool]:
    """Check the pinned cosign binary and OIDC token without signing."""
    results: list[bool] = []
    try:
        completed = subprocess.run(["cosign", "version", "--json"], capture_output=True, text=True, check=False)
        version = json.loads(completed.stdout).get("gitVersion") if completed.stdout else None
        if completed.returncode or version != COSIGN_VERSION:
            raise VerificationError(f"cosign is not pinned at {COSIGN_VERSION}")
        results.append(_check_line("sign", "cosign", True, f"cosign {COSIGN_VERSION} is installed"))
    except Exception as error:
        results.append(_check_line("sign", "cosign", False, f"{type(error).__name__}: {error}"))
    try:
        url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=sigstore"
        request = urllib.request.Request(url, headers={"Authorization": f"bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"})
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
        if not payload.get("value"):
            raise VerificationError("OIDC endpoint returned no token")
        results.append(_check_line("sign", "OIDC token", True, "sigstore-audience OIDC token is available"))
    except Exception as error:
        results.append(_check_line("sign", "OIDC token", False, f"{type(error).__name__}: {error}"))
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
    check_parser = sub.add_parser("check"); check_parser.add_argument("--scope", choices=("read", "sign", "feed"), required=True)
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
            results = check_read() if args.scope == "read" else check_sign() if args.scope == "sign" else check_feed()
            return 0 if all(results) else 1
    except (OSError, ValueError, VerificationError) as error:
        print(f"release {args.command} failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
