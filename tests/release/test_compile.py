import json
from pathlib import Path

import pytest

from scripts.release.compile import compile_bundle


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
    first = compile_bundle(*inputs)
    first_bytes = inputs[-1].read_bytes()
    second = compile_bundle(*inputs)
    assert second == first
    assert inputs[-1].read_bytes() == first_bytes


@pytest.mark.parametrize("kind", ["missing", "image", "extra"])
def test_compiler_rejects_manifest_compose_mismatch(tmp_path, kind):
    compose = json.loads((ROOT / "compose.json").read_text())
    manifest = (ROOT / "manifest.yaml").read_text()
    if kind == "missing":
        del compose["services"]["beta"]
    elif kind == "image":
        manifest = manifest.replace("registry.invalid/beta@sha256:bbbb", "registry.invalid/beta@sha256:aaaaaaaa")
    else:
        compose["services"]["gamma"] = {"image": "registry.invalid/gamma@sha256:" + "c" * 64}
    compose_path = tmp_path / "compose.json"
    compose_path.write_text(json.dumps(compose))
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(manifest)
    args = (compose_path, manifest_path, ROOT / "probes.json", ROOT / "policy.yaml", ROOT / "files", tmp_path / "bundle.tar")
    expected = "missing from Compose: beta" if kind == "missing" else None
    with pytest.raises(ValueError, match=expected):
        compile_bundle(*args)
