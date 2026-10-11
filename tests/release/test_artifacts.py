import hashlib
import json
from io import BytesIO
from pathlib import Path
import tarfile

import pytest
import yaml

from scripts.release.artifacts import (
    admit_artifact,
    load_approved_destinations,
    publish_artifact,
    verify_oci_archive,
)
from scripts.release.contracts import ValidationError


def _provenance(**overrides):
    value = {
        "schema": 1,
        "repository": "acme/homelab",
        "workflow_ref": "refs/heads/main",
        "run_id": 42,
        "run_attempt": 2,
        "source_sha": "a" * 40,
        "image": "web",
        "manifest_digest": "sha256:" + "b" * 64,
        "built_at": "2026-09-27T00:00:00Z",
    }
    value.update(overrides)
    return value


def _expected():
    return {
        "repository": "acme/homelab",
        "workflow_ref": "refs/heads/main",
        "run_id": 42,
        "run_attempt": 2,
        "source_sha": "a" * 40,
        "images": ["web", "api"],
    }


def _oci_result():
    return {"manifest_digest": "sha256:" + "b" * 64}


def test_valid_provenance_is_admitted():
    assert admit_artifact(_provenance(), _expected(), _oci_result()) is None


@pytest.mark.parametrize("field", ["repository", "workflow_ref", "run_id", "run_attempt", "source_sha"])
def test_rejects_provenance_mismatch(field):
    values = {
        "repository": "acme/other",
        "workflow_ref": "refs/heads/release",
        "run_id": 43,
        "run_attempt": 3,
        "source_sha": "c" * 40,
    }
    with pytest.raises(ValidationError):
        admit_artifact(_provenance(**{field: values[field]}), _expected(), _oci_result())


def test_rejects_unexpected_image():
    with pytest.raises(ValidationError):
        admit_artifact(_provenance(image="worker"), _expected(), _oci_result())


def test_rejects_manifest_digest_mismatch():
    with pytest.raises(ValidationError):
        admit_artifact(_provenance(), _expected(), {"manifest_digest": "sha256:" + "c" * 64})


def _archive(members):
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, value in members:
            info = tarfile.TarInfo(name)
            if isinstance(value, tuple):
                info.type = value[0]
                archive.addfile(info, BytesIO(value[1]))
            else:
                info.size = len(value)
                archive.addfile(info, BytesIO(value))
    return output.getvalue()


def _layout():
    config = b"config"
    layer = b"layer contents"

    def digest(value):
        return "sha256:" + hashlib.sha256(value).hexdigest()

    config_digest = digest(config)
    layer_digest = digest(layer)
    manifest = json.dumps({
        "schemaVersion": 2,
        "config": {"digest": config_digest, "size": len(config)},
        "layers": [{"digest": layer_digest, "size": len(layer)}],
    }, separators=(",", ":")).encode()
    manifest_digest = digest(manifest)
    archive = _archive([
        ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
        ("index.json", json.dumps({
            "manifests": [{"digest": manifest_digest, "size": len(manifest)}],
        }).encode()),
        ("blobs/sha256/" + manifest_digest[7:], manifest),
        ("blobs/sha256/" + config_digest[7:], config),
        ("blobs/sha256/" + layer_digest[7:], layer),
    ])
    return archive, manifest_digest, config_digest, layer_digest


def test_valid_oci_layout_is_accepted():
    archive, manifest, config, layer = _layout()
    assert verify_oci_archive(archive, manifest) == {
        "manifest_digest": manifest,
        "config_digest": config,
        "layer_digests": [layer],
    }


@pytest.mark.parametrize("member", [
    ("link", (tarfile.SYMTYPE, b"oci-layout")),
    ("../oci-layout", b"bad"),
    ("/oci-layout", b"bad"),
    ("unexpected", b"bad"),
])
def test_rejects_invalid_member(member):
    archive, manifest, _, _ = _layout()
    del manifest
    with pytest.raises(ValidationError):
        verify_oci_archive(_archive([("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'), member]), "sha256:" + "a" * 64)


def test_rejects_duplicate_member():
    archive, manifest, _, _ = _layout()
    duplicate = _archive([("oci-layout", b"{}"), ("oci-layout", b"{}")])
    with pytest.raises(ValidationError):
        verify_oci_archive(duplicate, manifest)


def test_rejects_blob_content_not_matching_name():
    archive, manifest, config, _ = _layout()
    broken = _archive([
        ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
        ("index.json", json.dumps({"manifests": [{"digest": manifest, "size": 3}]}).encode()),
        ("blobs/sha256/" + manifest[7:], b"bad"),
        ("blobs/sha256/" + config[7:], b"wrong"),
    ])
    with pytest.raises(ValidationError):
        verify_oci_archive(broken, manifest)


@pytest.mark.parametrize("change", ["digest", "missing", "size"])
def test_rejects_invalid_reachable_blob(change):
    archive, manifest, config, layer = _layout()
    del archive, config
    layer_bytes = b"layer contents"
    bad_digest = "sha256:" + "b" * 64
    manifest_data = json.dumps({
        "config": {"digest": "sha256:" + hashlib.sha256(b"config").hexdigest(), "size": 6},
        "layers": [{"digest": bad_digest if change == "digest" else layer, "size": 999 if change == "size" else len(layer_bytes)}],
    }).encode()
    manifest = "sha256:" + hashlib.sha256(manifest_data).hexdigest()
    members = [
        ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
        ("index.json", json.dumps({"manifests": [{"digest": manifest, "size": len(manifest_data)}]}).encode()),
        ("blobs/sha256/" + manifest[7:], manifest_data),
        ("blobs/sha256/" + hashlib.sha256(b"config").hexdigest(), b"config"),
    ]
    if change != "missing":
        members.append(("blobs/sha256/" + layer[7:], layer_bytes))
    with pytest.raises(ValidationError):
        verify_oci_archive(_archive(members), manifest)


@pytest.mark.parametrize("kind", ["layout", "substitution", "two-manifests"])
def test_rejects_invalid_index_or_layout(kind):
    _, manifest, _, _ = _layout()
    layout = b'{"imageLayoutVersion":"0.1.0"}' if kind == "layout" else b'{"imageLayoutVersion":"1.0.0"}'
    index = {"manifests": [{"digest": manifest, "size": 0}]}
    if kind == "substitution":
        index["manifests"][0]["digest"] = "sha256:" + "c" * 64
    elif kind == "two-manifests":
        index["manifests"].append(index["manifests"][0].copy())
    with pytest.raises(ValidationError):
        verify_oci_archive(_archive([("oci-layout", layout), ("index.json", json.dumps(index).encode())]), manifest)


def test_rejects_destination_not_in_approved_list(tmp_path: Path):
    archive, manifest, _, _ = _layout()
    artifact = tmp_path / "oci-layout.tar"
    artifact.write_bytes(archive)
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps({"destinations": ["ghcr.io/acme/homelab/web"]}))

    with pytest.raises(ValidationError, match="approved"):
        publish_artifact(
            artifact,
            "ghcr.io/acme/homelab/api",
            manifest,
            approved_path=approved,
        )


def test_publish_rejects_hash_mismatch(tmp_path: Path):
    archive, manifest, _, _ = _layout()
    artifact = tmp_path / "oci-layout.tar"
    artifact.write_bytes(archive)
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps({"destinations": ["ghcr.io/acme/homelab/web"]}))

    with pytest.raises(ValidationError):
        publish_artifact(
            artifact,
            "ghcr.io/acme/homelab/web",
            "sha256:" + "c" * 64,
            approved_path=approved,
        )


def test_publish_preserves_digest_and_does_not_unpack(tmp_path: Path):
    archive, manifest, _, _ = _layout()
    artifact = tmp_path / "oci-layout.tar"
    artifact.write_bytes(archive)
    approved = tmp_path / "approved.json"
    destination = "ghcr.io/acme/homelab/web"
    approved.write_text(json.dumps({"destinations": [destination]}))
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return type("Completed", (), {"stdout": manifest + "\n", "returncode": 0})()

    assert publish_artifact(
        artifact,
        destination,
        manifest,
        approved_path=approved,
        runner=runner,
    ) == manifest
    assert commands[0][:3] == ["skopeo", "copy", "--preserve-digests"]
    assert commands[1][:3] == ["skopeo", "inspect", "--format"]
    assert all("docker build" not in " ".join(command) for command in commands)


def test_publish_rejects_authenticated_destination_digest_mismatch(tmp_path: Path, monkeypatch):
    archive, manifest, _, _ = _layout()
    artifact = tmp_path / "oci-layout.tar"
    artifact.write_bytes(archive)
    approved = tmp_path / "approved.json"
    destination = "ghcr.io/acme/homelab/web"
    approved.write_text(json.dumps({"destinations": [destination]}))
    monkeypatch.setenv("GHCR_PUBLISH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_ACTOR", "test-actor")
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        stdout = manifest + "\n" if command[1] == "copy" else "sha256:" + "c" * 64 + "\n"
        return type("Completed", (), {"stdout": stdout, "returncode": 0})()

    with pytest.raises(ValidationError, match="does not match artifact manifest digest"):
        publish_artifact(
            artifact,
            destination,
            manifest,
            approved_path=approved,
            runner=runner,
        )
    assert commands[1][0:4] == ["skopeo", "inspect", "--creds", "test-actor:test-token"]


def test_approved_destinations_are_a_fixed_list(tmp_path: Path):
    config = tmp_path / "approved.json"
    config.write_text(json.dumps({"destinations": ["ghcr.io/acme/homelab/web"]}))
    assert load_approved_destinations(config) == frozenset({"ghcr.io/acme/homelab/web"})
    config.write_text(json.dumps({"destinations": ["ghcr.io/acme/homelab/web", "ghcr.io/acme/homelab/web"]}))
    with pytest.raises(ValidationError):
        load_approved_destinations(config)


def test_build_workflow_has_no_credentials_references():
    workflow = yaml.safe_load(
        (Path(__file__).parents[2] / ".github/workflows/reusable-build.yml").read_text()
    )
    serialized = json.dumps(workflow).lower()
    assert workflow["permissions"] == {"contents": "read"}
    assert "secrets" not in serialized
    assert "environment" not in serialized
    assert all(
        step.get("with", {}).get("persist-credentials") != True
        for step in workflow["jobs"]["build"]["steps"]
    )
