"""Validation for OCI image-layout archives produced by buildx."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import argparse
import os
from pathlib import Path
import re
import subprocess
import tarfile
from typing import Any

from .contracts import (
    ValidationError,
    parse_json,
    validate_build_provenance,
    validate_digest,
)


_BLOB_NAME = re.compile(r"^blobs/sha256/([0-9a-f]{64})$")
_ALLOWED_MEMBERS = {"oci-layout", "index.json"}
_DESTINATION = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9._/-]*$")
_DEFAULT_APPROVED_DESTINATIONS = Path(__file__).parents[2] / "config/release/approved-ghcr-destinations.json"


def admit_artifact(provenance: dict[str, Any], expected: dict[str, Any], oci_result: dict[str, Any]) -> None:
    """Admit an OCI artifact only when its build provenance matches expectations."""
    provenance = validate_build_provenance(provenance)
    if not isinstance(expected, dict):
        raise ValidationError("$.expected", "expected object")
    if not isinstance(oci_result, dict):
        raise ValidationError("$.oci_result", "expected object")

    for field in ("repository", "workflow_ref", "run_id", "run_attempt", "source_sha"):
        if field not in expected:
            raise ValidationError(f"$.expected.{field}", "missing required field")
        if provenance[field] != expected[field]:
            raise ValidationError(f"$.{field}", "does not match expected value")

    images = expected.get("images")
    if not isinstance(images, (list, tuple, set, frozenset, dict)):
        raise ValidationError("$.expected.images", "expected image name collection")
    if provenance["image"] not in images:
        raise ValidationError("$.image", "is not an expected image")

    if "manifest_digest" not in oci_result:
        raise ValidationError("$.oci_result.manifest_digest", "missing required field")
    if provenance["manifest_digest"] != oci_result["manifest_digest"]:
        raise ValidationError("$.manifest_digest", "does not match OCI result")


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(path, "expected object")
    return value


def _member_data(archive: tarfile.TarFile, member: tarfile.TarInfo, path: str) -> bytes:
    try:
        stream = archive.extractfile(member)
        if stream is None:
            raise ValidationError(path, "expected regular file")
        return stream.read()
    except ValidationError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise ValidationError(path, "could not read member") from error


def _descriptor(value: Any, path: str) -> tuple[str, int]:
    descriptor = _object(value, path)
    if "digest" not in descriptor:
        raise ValidationError(f"{path}.digest", "missing required field")
    digest = validate_digest(descriptor["digest"], f"{path}.digest")
    size = descriptor.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValidationError(f"{path}.size", "expected nonnegative integer")
    return digest, size


def _json(data: bytes, path: str) -> Any:
    try:
        return parse_json(data)
    except ValidationError as error:
        raise ValidationError(path + error.path[1:], error.message) from error


def verify_oci_archive(data: bytes, expected_manifest_digest: str | None = None) -> dict[str, Any]:
    """Verify an OCI image-layout archive and return its reachable digests."""
    if expected_manifest_digest is not None:
        expected_manifest_digest = validate_digest(expected_manifest_digest, "$.expected_manifest_digest")

    try:
        archive = tarfile.open(fileobj=BytesIO(data), mode="r:")
    except (OSError, EOFError, tarfile.TarError, TypeError) as error:
        raise ValidationError("$", "invalid tar archive") from error

    members: dict[str, tarfile.TarInfo] = {}
    contents: dict[str, bytes] = {}
    try:
        for index, member in enumerate(archive):
            path = f"$.members[{index}]"
            name = member.name
            if name.startswith("/") or any(part == ".." for part in name.split("/")):
                raise ValidationError(path, f"invalid archive path: {name!r}")
            if name in members:
                raise ValidationError(path, f"duplicate archive member: {name!r}")
            if not member.isreg():
                raise ValidationError(path, f"member must be a regular file: {name!r}")
            if name not in _ALLOWED_MEMBERS and _BLOB_NAME.fullmatch(name) is None:
                raise ValidationError(path, f"unexpected archive member: {name!r}")
            members[name] = member
            contents[name] = _member_data(archive, member, path)
    except ValidationError:
        raise
    except (OSError, EOFError, tarfile.TarError) as error:
        raise ValidationError("$.members", "invalid tar archive") from error
    finally:
        archive.close()

    for required in ("oci-layout", "index.json"):
        if required not in members:
            raise ValidationError(f"$.members.{required}", "missing required member")

    for name, blob in contents.items():
        match = _BLOB_NAME.fullmatch(name)
        if match is not None and match.group(1) != sha256(blob).hexdigest():
            raise ValidationError(f"$.blobs.{match.group(1)}", "content does not match blob name")

    layout = _object(_json(contents["oci-layout"], "$.oci_layout"), "$.oci_layout")
    if layout != {"imageLayoutVersion": "1.0.0"}:
        raise ValidationError("$.oci_layout", "expected imageLayoutVersion 1.0.0")

    index = _object(_json(contents["index.json"], "$.index"), "$.index")
    manifests = index.get("manifests")
    if not isinstance(manifests, list):
        raise ValidationError("$.index.manifests", "expected array")
    if len(manifests) != 1:
        raise ValidationError("$.index.manifests", "expected exactly one manifest")
    manifest_digest, manifest_size = _descriptor(manifests[0], "$.index.manifests[0]")
    if expected_manifest_digest is not None and manifest_digest != expected_manifest_digest:
        raise ValidationError("$.index.manifests[0].digest", "does not match expected manifest digest")

    manifest_name = "blobs/sha256/" + manifest_digest.removeprefix("sha256:")
    if manifest_name not in contents:
        raise ValidationError("$.index.manifests[0].digest", "manifest blob is missing")
    manifest_data = contents[manifest_name]
    if len(manifest_data) != manifest_size:
        raise ValidationError("$.index.manifests[0].size", "does not match blob size")
    manifest = _object(_json(manifest_data, "$.manifest"), "$.manifest")

    config_digest, config_size = _descriptor(manifest.get("config"), "$.manifest.config")
    layers = manifest.get("layers")
    if not isinstance(layers, list):
        raise ValidationError("$.manifest.layers", "expected array")
    layer_digests: list[str] = []
    descriptors = [(config_digest, config_size, "$.manifest.config")]
    for index, layer in enumerate(layers):
        digest, size = _descriptor(layer, f"$.manifest.layers[{index}]")
        layer_digests.append(digest)
        descriptors.append((digest, size, f"$.manifest.layers[{index}]"))

    for digest, size, path in descriptors:
        blob_name = "blobs/sha256/" + digest.removeprefix("sha256:")
        blob = contents.get(blob_name)
        if blob is None:
            raise ValidationError(f"{path}.digest", "blob is missing")
        if len(blob) != size:
            raise ValidationError(f"{path}.size", "does not match blob size")

    return {
        "manifest_digest": manifest_digest,
        "config_digest": config_digest,
        "layer_digests": layer_digests,
    }


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


def publish_artifact(
    artifact: str | Path,
    destination: str,
    expected_manifest_digest: str,
    *,
    approved_path: str | Path = _DEFAULT_APPROVED_DESTINATIONS,
    provenance: dict[str, Any] | None = None,
    expected: dict[str, Any] | None = None,
    runner=subprocess.run,
) -> str:
    """Validate and byte-copy one OCI archive to an approved GHCR repository."""
    expected_manifest_digest = validate_digest(expected_manifest_digest, "$.expected_manifest_digest")
    if destination not in load_approved_destinations(approved_path):
        raise ValidationError("$.destination", "is not an approved destination")
    if provenance is not None or expected is not None:
        if provenance is None or expected is None:
            raise ValidationError("$.provenance", "provenance and expected values are both required")
        admit_artifact(provenance, expected, {"manifest_digest": expected_manifest_digest})

    artifact_path = Path(artifact)
    if artifact_path.is_dir():
        artifact_path /= "oci-layout.tar"
    try:
        archive = artifact_path.read_bytes()
    except OSError as error:
        raise ValidationError("$.artifact", "could not read artifact") from error
    verify_oci_archive(archive, expected_manifest_digest)

    source = f"oci-archive:{artifact_path}"
    target = f"docker://{destination}@{expected_manifest_digest}"
    copy_command = ["skopeo", "copy", "--preserve-digests", source, target]
    token = os.environ.get("GHCR_PUBLISH_TOKEN")
    if token:
        copy_command[3:3] = ["--dest-creds", f"{os.environ.get('GITHUB_ACTOR', 'github-actions')}:{token}"]
    try:
        runner(copy_command, check=True, capture_output=True, text=True)
        inspect_command = ["skopeo", "inspect", "--format", "{{.Digest}}", target]
        if token:
            inspect_command[2:2] = ["--creds", f"{os.environ.get('GITHUB_ACTOR', 'github-actions')}:{token}"]
        inspected = runner(
            inspect_command,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValidationError("$.destination", "registry copy or verification failed") from error
    destination_digest = inspected.stdout.strip()
    if destination_digest != expected_manifest_digest:
        raise ValidationError("$.destination_digest", "does not match artifact manifest digest")
    return destination_digest


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--artifact", required=True, type=Path)
    publish.add_argument("--destination", required=True)
    publish.add_argument("--expected-manifest-digest", required=True)
    publish.add_argument("--approved-path", type=Path, default=_DEFAULT_APPROVED_DESTINATIONS)
    args = parser.parse_args()
    if args.command == "publish":
        publish_artifact(
            args.artifact,
            args.destination,
            args.expected_manifest_digest,
            approved_path=args.approved_path,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
