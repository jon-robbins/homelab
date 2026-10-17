import io
import json
import tarfile

from scripts.release.bundle import build, bundle_sha256, verify


def test_v2_bundle_is_deterministic(tmp_path):
    for name, value in (("app.json", {"services": {"a": {}}}), ("edge.json", {}), ("site.yaml", "contract: 1\n")):
        path = tmp_path / name
        path.write_text(json.dumps(value, sort_keys=True) if isinstance(value, dict) else value)
    first = build(tmp_path)
    verify(first)
    second = build(tmp_path)
    assert first == second
    assert bundle_sha256(first).startswith("sha256:")
    with tarfile.open(fileobj=io.BytesIO(first)) as archive:
        assert archive.getnames() == ["app.json", "edge.json", "site.yaml"]


def test_v2_bundle_rejects_legacy_shape(tmp_path):
    (tmp_path / "app.json").write_text("{}")
    (tmp_path / "edge.json").write_text("{}")
    (tmp_path / "applications.json").write_text("{}")
    try:
        build(tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("legacy bundle shape was accepted")
