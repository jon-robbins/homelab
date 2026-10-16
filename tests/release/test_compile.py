import json
from pathlib import Path
import tarfile

import pytest

from scripts.release.compile import compile_bundle
from scripts.release.contracts import ValidationError


ROOT = Path(__file__).parent / "fixtures" / "site"


def paths(tmp_path):
    return (
        ROOT / "compose.json",
        ROOT / "manifest.yaml",
        ROOT / "probes.json",
        ROOT / "policy.yaml",
        ROOT / "files",
        tmp_path / "bundle.tar",
    )


def test_fixture_compiles_deterministically(tmp_path):
    inputs = paths(tmp_path)
    image_refs = {
        "alpha": "ghcr.io/jon-robbins/homelab/caddy@sha256:" + "a" * 64,
        "beta": "ghcr.io/jon-robbins/homelab/homelab-workers@sha256:" + "b" * 64,
    }
    first = compile_bundle(*inputs, image_refs)
    first_bytes = inputs[-1].read_bytes()
    second = compile_bundle(*inputs, image_refs)
    assert second == first
    assert inputs[-1].read_bytes() == first_bytes
    with tarfile.open(inputs[-1], "r") as bundle:
        applications = json.loads(bundle.extractfile("applications.json").read())
        compose = bundle.extractfile("compose.yaml").read().decode()
    assert {item["name"]: item["image"] for item in applications["applications"]} == image_refs
    assert all(reference in compose for reference in image_refs.values())


@pytest.mark.parametrize(
    "image",
    [
        "docker.io/foo:latest",
        "ghcr.io/jon-robbins/homelab/caddy:latest",
        "ghcr.io/jon-robbins/homelab/caddy@sha256:" + "c" * 64,
    ],
)
def test_compiler_rejects_image_on_unmanaged_service(tmp_path, image):
    compose = json.loads((ROOT / "compose.json").read_text())
    compose["x-homelab-managed-services"] = ["alpha", "beta"]
    compose["services"]["unmanaged"] = {"image": image}
    compose_path = tmp_path / "compose.json"
    compose_path.write_text(json.dumps(compose))
    args = (compose_path, ROOT / "manifest.yaml", ROOT / "probes.json", ROOT / "policy.yaml", ROOT / "files", tmp_path / "bundle.tar")

    with pytest.raises(ValueError, match="image is not permitted for an unmanaged service|must be a bare approved GHCR repository"):
        compile_bundle(*args, _image_refs())


def _image_refs():
    return {
        "alpha": "ghcr.io/jon-robbins/homelab/caddy@sha256:" + "a" * 64,
        "beta": "ghcr.io/jon-robbins/homelab/homelab-workers@sha256:" + "b" * 64,
    }


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("tagged", "must be a bare approved GHCR repository"),
        ("digested", "must be a bare approved GHCR repository"),
        ("unapproved", "must be a bare approved GHCR repository"),
        ("missing", "differs at beta"),
        ("extra", "differs at gamma"),
    ],
)
def test_compiler_rejects_invalid_image_refs(tmp_path, kind, expected):
    compose = json.loads((ROOT / "compose.json").read_text())
    manifest = (ROOT / "manifest.yaml").read_text()
    if kind == "tagged":
        image = "ghcr.io/jon-robbins/homelab/caddy:latest"
        compose["services"]["alpha"]["image"] = image
        manifest = manifest.replace("ghcr.io/jon-robbins/homelab/caddy", image)
    elif kind == "digested":
        image = "ghcr.io/jon-robbins/homelab/caddy@sha256:" + "c" * 64
        compose["services"]["alpha"]["image"] = image
        manifest = manifest.replace("ghcr.io/jon-robbins/homelab/caddy", image)
    elif kind == "unapproved":
        image = "ghcr.io/jon-robbins/homelab/not-approved"
        compose["services"]["alpha"]["image"] = image
        manifest = manifest.replace("ghcr.io/jon-robbins/homelab/caddy", image)

    compose_path = tmp_path / "compose.json"
    compose_path.write_text(json.dumps(compose))
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(manifest)
    image_refs = _image_refs()
    if kind == "missing":
        del image_refs["beta"]
    elif kind == "extra":
        image_refs["gamma"] = "ghcr.io/jon-robbins/homelab/caddy@sha256:" + "c" * 64

    args = (compose_path, manifest_path, ROOT / "probes.json", ROOT / "policy.yaml", ROOT / "files", tmp_path / "bundle.tar")
    with pytest.raises(ValueError, match=expected):
        compile_bundle(*args, image_refs)


@pytest.mark.parametrize("kind", ["missing", "image", "extra"])
def test_compiler_rejects_manifest_compose_mismatch(tmp_path, kind):
    compose = json.loads((ROOT / "compose.json").read_text())
    manifest = (ROOT / "manifest.yaml").read_text()
    if kind == "missing":
        del compose["services"]["beta"]
    elif kind == "image":
        manifest = manifest.replace("ghcr.io/jon-robbins/homelab/homelab-workers", "ghcr.io/jon-robbins/homelab/caddy")
    else:
        compose["services"]["gamma"] = {"image": "registry.invalid/gamma@sha256:" + "c" * 64}
    compose_path = tmp_path / "compose.json"
    compose_path.write_text(json.dumps(compose))
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(manifest)
    args = (compose_path, manifest_path, ROOT / "probes.json", ROOT / "policy.yaml", ROOT / "files", tmp_path / "bundle.tar")
    expected = "missing from Compose: beta" if kind == "missing" else None
    with pytest.raises(ValueError, match=expected):
        compile_bundle(*args, {"alpha": "ghcr.io/jon-robbins/homelab/caddy@sha256:" + "a" * 64, "beta": "ghcr.io/jon-robbins/homelab/homelab-workers@sha256:" + "b" * 64})


def test_compiler_reports_malformed_manifest_as_validation_error(tmp_path):
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("schema: 1\nsite_id: s\napplications:\n  - image: ghcr.io/jon-robbins/homelab/caddy\n", encoding="utf-8")
    args = (ROOT / "compose.json", manifest_path, ROOT / "probes.json", ROOT / "policy.yaml", ROOT / "files", tmp_path / "bundle.tar")
    with pytest.raises(ValidationError, match="malformed application manifest"):
        compile_bundle(*args, {})


def test_compiler_uses_site_roots_and_edge_context(tmp_path):
    compose = json.loads((ROOT / "compose.json").read_text())
    compose["services"]["alpha"]["network_mode"] = "host"
    compose_path = tmp_path / "compose.json"
    compose_path.write_text(json.dumps(compose))
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(json.dumps({
        "minimum_rollback": {},
        "required_probes": {},
        "gpu_profiles": [],
        "site_env": {"APP_DATA_DIR": str(tmp_path / "data")},
        "bind_roots": ["APP_DATA_DIR"],
        "module_path": "modules/edge/cloudflared.yml",
    }))
    args = (compose_path, ROOT / "manifest.yaml", ROOT / "probes.json", policy_path, ROOT / "files", tmp_path / "bundle.tar")
    compile_bundle(*args, _image_refs())


def test_compiler_rejects_bind_outside_site_roots(tmp_path):
    compose = json.loads((ROOT / "compose.json").read_text())
    compose["services"]["alpha"]["volumes"] = [{
        "type": "bind", "source": "release-data", "target": "/data",
        "bind": {"create_host_path": False},
    }]
    compose_path = tmp_path / "compose.json"
    compose_path.write_text(json.dumps(compose))
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(json.dumps({
        "minimum_rollback": {},
        "required_probes": {},
        "gpu_profiles": [],
        "site_env": {"APP_DATA_DIR": str(tmp_path / "declared")},
        "bind_roots": ["APP_DATA_DIR"],
    }))
    args = (compose_path, ROOT / "manifest.yaml", ROOT / "probes.json", policy_path, ROOT / "files", tmp_path / "bundle.tar")
    with pytest.raises(ValidationError, match="outside declared site roots"):
        compile_bundle(*args, _image_refs())
