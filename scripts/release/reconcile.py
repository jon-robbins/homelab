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

import yaml

from .bundle import bundle_sha256, verify
from .contracts import ValidationError, load_release_record, parse_json, validate_digest
from .feed import FeedError, publish_next, verify_cosign_bundle
from .github import GitHubClient, GitHubError
from .render import render
from .sign import COSIGN_VERSION, record_bytes, sign_record
from .verify import VerificationError, VerificationResult, verify_release
SITE_REPOSITORY = os.environ.get("SITE_REPOSITORY", "jon-robbins/homelab-site")
BUILD_WORKFLOW = ".github/workflows/post-merge-build.yml"
# Paths whose change makes the build workflow build an image; test_workflow keeps them in step with its filters.
BUILD_INPUT_PREFIXES = (
    ".github/workflows/post-merge-build.yml",
    "config/release/approved-ghcr-destinations.json",
    "config/caddy/",
    "config/seerr-oidc/",
)
# The commit API lists at most this many files per page; a full page may be truncated.
COMMIT_FILES_LIMIT = 300
RELEASE_INPUTS_DIR_DEFAULT = "release-inputs"
_DESTINATION = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9._/-]*$")
_DEFAULT_APPROVED_DESTINATIONS = Path(__file__).parents[2] / "config/release/approved-ghcr-destinations.json"


def render_release(client: Any, platform: Path, site_repository: str, site_sha: str,
                   homelab_sha: str, output: Path,
                   source_shas: Mapping[str, str] | None = None) -> tuple[dict[str, str], Any]:
    """Fetch the declared plugin trees and render the site; site.yaml is kept next to *output* for compile."""

    with tempfile.TemporaryDirectory(prefix="release-sources-") as directory:
        root = Path(directory)
        site = client.fetch(site_repository, site_sha, root / "site")
        site_document = yaml.safe_load((site / "site.yaml").read_bytes())
        plugins: dict[str, Path] = {}
        sources = {"homelab": homelab_sha, "homelab-site": site_sha}
        for name, spec in (site_document.get("plugins") or {}).items():
            if "path" in spec:
                plugins[name] = site / spec["path"]
                sources[name] = site_sha
            elif "repository" in spec:
                repository = spec["repository"]
                sha = (source_shas or {}).get(name) or client.ref(repository, "main")
                plugins[name] = client.fetch(repository, sha, root / "plugins" / name)
                sources[name] = sha
        rendered = render(platform, site, plugins)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_name("site.yaml").write_bytes((site / "site.yaml").read_bytes())
        return sources, rendered


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


def _verify_attestation(archive: Path, repository: str, sha: str) -> None:
    """Require GitHub's signed build attestation for a downloaded archive."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    command = [
        "gh", "attestation", "verify", str(archive),
        "--repo", repository,
        "--signer-workflow", f"{repository}/{BUILD_WORKFLOW}",
        "--source-digest", sha,
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "attestation verification failed").strip()
        raise VerificationError(f"invalid build attestation: {detail[:300]}")


def _read_manifest_digest(archive: Path) -> str:
    """Read an OCI archive's manifest digest with standard OCI tooling."""
    try:
        completed = subprocess.run(
            ["skopeo", "inspect", "--format", "{{.Digest}}", f"oci-archive:{archive}"],
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
    if "bypass_actors" not in detail:
        # GitHub omits bypass_actors for callers without Administration read.
        raise VerificationError("cannot read homelab main ruleset bypass actors; PRIVATE_INPUT_READ_TOKEN needs Administration: Read on homelab")
    if detail.get("enforcement") != "active" or detail.get("bypass_actors") != []:
        raise VerificationError("homelab main ruleset is weakened")
    if detail.get("target") != "branch":
        raise VerificationError("homelab main ruleset is not branch-targeted")
    ref_name = detail.get("conditions", {}).get("ref_name", {})
    include, exclude = set(ref_name.get("include", ())), set(ref_name.get("exclude", ()))
    default_is_main = bool(include & {"~DEFAULT_BRANCH"} or exclude & {"~DEFAULT_BRANCH"}) and client.repository(repository).get("default_branch") == "main"
    main_names = {"refs/heads/main", "~ALL"} | ({"~DEFAULT_BRANCH"} if default_is_main else set())
    if not include & main_names or exclude & (main_names | {"refs/heads/main"}):
        raise VerificationError("homelab main ruleset does not target main")
    types = {rule.get("type") for rule in detail.get("rules", ())}
    if not {"pull_request", "non_fast_forward", "deletion"}.issubset(types):
        raise VerificationError("homelab main ruleset does not require PR and block force-push/deletion")


def _check_homelab_head_ancestry(client: Any, repository: str, sha: str) -> None:
    """Ensure the run commit is still on main, allowing main to advance."""
    if not hasattr(client, "compare_commits"):
        return
    live_head = client.ref(repository, "main")
    comparison = client.compare_commits(repository, sha, live_head)
    if comparison.get("status") not in ("ahead", "identical"):
        raise VerificationError("workflow commit is not an ancestor of the current homelab main")


BOOTSTRAP_SEARCH_DEPTH = 50


def _successful_build_run_id(client: Any, repository: str, sha: str) -> int | None:
    """Return the successful main push run of the build workflow for *sha*, if any."""
    response = client.workflow_runs(repository, BUILD_WORKFLOW, sha)
    runs = response.get("workflow_runs", ()) if isinstance(response, Mapping) else response
    for run in runs or ():
        if (
            isinstance(run, Mapping)
            and run.get("head_sha") == sha
            and run.get("event") == "push"
            and run.get("head_branch") == "main"
            and run.get("path", BUILD_WORKFLOW) == BUILD_WORKFLOW
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and isinstance(run.get("id"), int)
            and not isinstance(run.get("id"), bool)
        ):
            return run["id"]
    return None


def _bootstrap_sha(client: Any, repository: str, head: str, images: set[str]) -> str:
    """Pick the first release's commit: the newest first-parent commit with a full build.

    Every image is still built and attested at that one commit; this only lets
    the first release survive merges that landed after the full rebuild.
    """
    if not callable(getattr(client, "workflow_runs", None)) or not callable(getattr(client, "read_commit", None)):
        return head
    response = client.artifacts(repository)
    artifacts = response.get("artifacts", ()) if isinstance(response, Mapping) else response
    sha = head
    for _ in range(BOOTSTRAP_SEARCH_DEPTH):
        run_id = _successful_build_run_id(client, repository, sha)
        if run_id is not None:
            built = {
                str(artifact.get("name", ""))[4:]
                for artifact in artifacts or ()
                if isinstance(artifact, Mapping)
                and not artifact.get("expired")
                and isinstance(artifact.get("workflow_run"), Mapping)
                and artifact["workflow_run"].get("id") == run_id
                and artifact["workflow_run"].get("head_sha") == sha
                and str(artifact.get("name", "")).startswith("oci-")
            }
            if images.issubset(built):
                return sha
        commit = client.read_commit(repository, sha)
        parents = commit.get("parents") if isinstance(commit, Mapping) else None
        if not isinstance(parents, (list, tuple)) or not parents:
            break
        parent = parents[0]
        sha = parent.get("sha") if isinstance(parent, Mapping) else parent
        if not isinstance(sha, str):
            raise VerificationError("homelab main has an invalid first-parent link")
    raise VerificationError(
        f"first release requires one main commit with verified builds for all images within {BOOTSTRAP_SEARCH_DEPTH} commits; "
        "push a change to the build recipe to rebuild every image"
    )


def _changes_no_build_input(client: Any, repository: str, sha: str) -> bool:
    """Return whether *sha* provably changes nothing the build workflow builds from.

    A GITHUB_TOKEN merge (Dependabot automerge) starts no push run; such a commit
    has no images to wait for, and waiting would block every later release.
    """
    read_commit = getattr(client, "read_commit", None)
    commit = read_commit(repository, sha) if callable(read_commit) else None
    if not isinstance(commit, Mapping):
        return False
    files, parents = commit.get("files"), commit.get("parents")
    if not isinstance(files, list) or len(files) >= COMMIT_FILES_LIMIT or not isinstance(parents, list) or len(parents) != 1:
        return False
    if not all(isinstance(item, Mapping) for item in files):
        return False
    # A rename out of a build path still changes that path.
    names = [item.get("filename") for item in files] + [item["previous_filename"] for item in files if "previous_filename" in item]
    return all(isinstance(name, str) and not name.startswith(BUILD_INPUT_PREFIXES) for name in names)


def _attested_builds(client: Any, repository: str, sha: str) -> dict[str, dict[str, Any]]:
    """Download OCI artifacts and make their native attestations authoritative."""
    runs_method = getattr(client, "workflow_runs", None)
    successful_run_id: int | None = None
    if callable(runs_method):
        response = runs_method(repository, BUILD_WORKFLOW, sha)
        runs = response.get("workflow_runs", ()) if isinstance(response, Mapping) else response
        exact_runs = [
            run for run in runs or ()
            if isinstance(run, Mapping)
            and run.get("head_sha") == sha
            and run.get("event") == "push"
            and run.get("head_branch") == "main"
            and run.get("path", BUILD_WORKFLOW) == BUILD_WORKFLOW
        ]
        if not exact_runs:
            if _changes_no_build_input(client, repository, sha):
                return {}
            raise VerificationError(f"post-merge build for {sha} is not visible yet; waiting")
        successful_run = next(
            (
                run for run in exact_runs
                if run.get("status") == "completed" and run.get("conclusion") == "success"
            ),
            None,
        )
        if successful_run is None:
            raise VerificationError(f"post-merge build for {sha} is still running or failed; waiting")
        successful_run_id = successful_run.get("id")
        if isinstance(successful_run_id, bool) or not isinstance(successful_run_id, int) or successful_run_id < 1:
            raise VerificationError(f"successful post-merge build for {sha} has no workflow run locator; waiting")
    response = client.artifacts(repository)
    artifacts = response.get("artifacts", ()) if isinstance(response, Mapping) else response
    candidates = []
    for artifact in artifacts or ():
        if not isinstance(artifact, Mapping) or artifact.get("expired"):
            continue
        workflow_run = artifact.get("workflow_run")
        artifact_head = workflow_run.get("head_sha") if isinstance(workflow_run, Mapping) else None
        if artifact_head != sha:
            continue
        artifact_run_id = workflow_run.get("id") if isinstance(workflow_run, Mapping) else artifact.get("run_id")
        if successful_run_id is not None and artifact_run_id != successful_run_id:
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
        # Large images are streamed to disk, never held in memory.
        with tempfile.TemporaryDirectory(prefix="release-oci-") as directory:
            try:
                archive = client.download_artifact_member(repository, artifact, "oci-layout.tar", Path(directory))
            except GitHubError as error:
                raise VerificationError(f"OCI archive is missing from {name}: {error}") from error
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
    return images


def _next_tooling_sha(client: Any, repository: str, head: str, previous: Mapping[str, Any] | None) -> str | None:
    """Return the oldest unreleased first-parent commit, or the head at bootstrap."""
    if previous is None:
        return head
    # The candidate position is the released homelab source, not the tooling revision.
    position = previous.get("sources", {}).get("homelab") or previous.get("tooling_git_sha")
    if not isinstance(position, str):
        raise VerificationError("previous release has no homelab tooling position")
    if position == head:
        return None
    if hasattr(client, "compare_commits"):
        comparison = client.compare_commits(repository, position, head)
        if comparison.get("status") not in ("ahead", "identical"):
            raise VerificationError("previous homelab release position is not an ancestor of main; refusing release")
    read_commit = getattr(client, "read_commit", None)
    if not callable(read_commit):
        return head
    sha = head
    after_position: list[str] = []
    while sha != position:
        commit = read_commit(repository, sha)
        if not isinstance(commit, Mapping):
            raise VerificationError(f"cannot read homelab first-parent history at {sha}")
        after_position.append(sha)
        parents = commit.get("parents")
        if not isinstance(parents, (list, tuple)) or not parents:
            raise VerificationError("previous homelab release position is not an ancestor of main; refusing release")
        parent = parents[0]
        sha = parent.get("sha") if isinstance(parent, Mapping) else parent
        if not isinstance(sha, str):
            raise VerificationError("homelab main has an invalid first-parent link")
    return after_position[-1]


def _destination_images(approved: set[str] | frozenset[str]) -> set[str]:
    prefix = "ghcr.io/jon-robbins/homelab/"
    return {destination.removeprefix(prefix) for destination in approved if destination.startswith(prefix)}

def plan(client: Any | None = None, *, repository: str | None = None, site_repository: str = SITE_REPOSITORY, output: Path | None = None) -> dict[str, Any]:
    client = client or GitHubClient()
    repository = repository or os.environ.get("GITHUB_REPOSITORY", "jon-robbins/homelab")
    _check_homelab_ruleset(client, repository)
    ancestry_client = GitHubClient(token=os.environ.get("GITHUB_TOKEN")) if os.environ.get("GITHUB_TOKEN") else client
    previous = client.last_record(site_repository, "releases")
    next_sequence = int(previous["sequence"]) + 1 if previous else 1
    main_head = os.environ.get("GITHUB_SHA")
    if not main_head:
        raise VerificationError("release plan requires GITHUB_SHA (run inside the release workflow)")
    _check_homelab_head_ancestry(ancestry_client, repository, main_head)
    sha = _next_tooling_sha(client, repository, main_head, previous)
    if previous is None:
        sha = _bootstrap_sha(client, repository, main_head, _destination_images(load_approved_destinations()))
    tooling_sha = sha or (previous.get("sources", {}).get("homelab") or previous.get("tooling_git_sha") if previous else main_head)
    site_sha = client.ref(site_repository, "main")
    site_yaml = client.read(site_repository, site_sha, "site.yaml")
    try:
        site_document = yaml.safe_load(site_yaml or b"")
    except yaml.YAMLError as error:
        raise VerificationError("site.yaml is not valid YAML") from error
    if not isinstance(site_document, Mapping) or not isinstance(site_document.get("site_id"), str):
        raise VerificationError("site.yaml has no site_id")
    plugin_repositories = {
        name: spec.get("repository", site_repository)
        for name, spec in (site_document.get("plugins") or {}).items()
        if isinstance(spec, Mapping) and (isinstance(spec.get("repository"), str) or isinstance(spec.get("path"), str))
    }
    input_root = Path(os.environ.get("RELEASE_INPUTS_DIR", RELEASE_INPUTS_DIR_DEFAULT))
    source_path = input_root / "bundle.tar"
    sources, rendered = render_release(client, Path("."), site_repository, site_sha, tooling_sha, source_path)
    if previous and previous.get("sources") == sources:
        result = {"release": False, "sha": tooling_sha, "sequence": next_sequence, "images": [],
                  "image_refs": previous.get("images", {}), "sources": sources}
    else:
        approved = load_approved_destinations()
        all_images = _destination_images(approved)
        homelab_changed = sha is not None
        if homelab_changed:
            builds = _attested_builds(client, repository, sha)
            builds = {image: spec for image, spec in builds.items() if spec.get("head_sha") == sha}
        else:
            builds = {}
        required = all_images if previous is None else set(builds)
        if previous is None and not all_images.issubset(builds):
            missing = sorted(all_images - set(builds))
            raise VerificationError(f"first release requires verified builds for all images; missing {missing[0]}")
        owners = set(os.environ.get("RELEASE_OWNERS", "jon-robbins").split(","))
        repositories = {"homelab": repository, "homelab-site": site_repository, **plugin_repositories}
        verified = verify_release(client, sources, builds, required_images=required, repositories=repositories,
                                  owners={"homelab": (), "homelab-site": owners, **{name: owners for name in plugin_repositories}},
                                  ruleset_repositories={"homelab"})
        if not isinstance(verified, VerificationResult):
            raise VerificationError("build is not complete")
        images = [{"image": image, "artifact_name": builds[image]["artifact_name"], "run_id": builds[image]["run_id"], "manifest_digest": builds[image]["manifest_digest"], "destination": f"ghcr.io/jon-robbins/homelab/{image}"} for image in sorted(verified.images)]
        if any(item["destination"] not in approved for item in images):
            raise VerificationError("build requested an unapproved destination")
        image_refs = dict(previous.get("images", {}) if previous else {})
        image_refs.update({item["image"]: f"{item['destination']}@{item['manifest_digest']}" for item in images})
        result = {"release": True, "sha": tooling_sha, "sequence": next_sequence, "images": images,
                  "image_refs": image_refs, "rollback_floor": previous.get("rollback_floor", 0) if previous else 0,
                  "sources": sources, "site_id": site_document["site_id"], "tooling_git_sha": main_head}
    from .compile import compile_bundle

    # Compile only now: first-party images are pinned to the digests this release built (D7).
    compile_bundle(rendered, source_path.with_name("site.yaml"), source_path, result["image_refs"])
    if output:
        output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return result


def sign_plan(plan_path: Path, output_dir: Path) -> None:
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan_data.get("release", bool(plan_data.get("images"))):
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "bundle.tar"
    source_bundle = Path(os.environ.get("RELEASE_INPUTS_DIR", RELEASE_INPUTS_DIR_DEFAULT)) / "bundle.tar"
    try:
        data = source_bundle.read_bytes()
        verify(data)
    except (OSError, ValueError) as error:
        raise VerificationError("release plan bundle is missing or invalid") from error
    bundle_path.write_bytes(data)
    image_refs = plan_data.get("image_refs", {})
    record = {"schema": 1, "site_id": plan_data["site_id"], "sequence": plan_data["sequence"],
              "tooling_git_sha": plan_data["tooling_git_sha"], "sources": plan_data["sources"],
              "images": image_refs, "bundle_sha256": bundle_sha256(data),
              "rollback_floor": plan_data.get("rollback_floor", 0)}
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
    for repository, label in ((SITE_REPOSITORY, "homelab-site head"),):
        try:
            heads[repository] = client.ref(repository, "main")
            results.append(_check_line("read", label, True, "default branch is readable"))
        except Exception as error:
            results.append(_check_line("read", label, False, f"{type(error).__name__}: {error}"))
    try:
        _check_homelab_ruleset(client, os.environ.get("GITHUB_REPOSITORY", "jon-robbins/homelab"))
        results.append(_check_line("read", "homelab main ruleset", True, "active, no bypass actors, PR and force-push/deletion rules"))
    except Exception as error:
        results.append(_check_line("read", "homelab main ruleset", False, f"{type(error).__name__}: {error}"))
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
