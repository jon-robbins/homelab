import json
import tarfile
from pathlib import Path

from scripts.release.compile import compile_bundle
from scripts.release.render import Rendered, render


def test_examples_compile_to_deterministic_v2_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.release.render.subprocess.run", lambda *a, **k: type(
        "Result", (), {"returncode": 0, "stdout": '{"services": {"whoami": {}}}', "stderr": ""}
    )())
    root = tmp_path / "bundle.tar"
    rendered = render(Path("."), Path("examples/site"), {"plugin-hello": Path("examples/plugin-hello")})
    compile_bundle(rendered, Path("examples/site/site.yaml"), root)
    first = root.read_bytes()
    compile_bundle(rendered, Path("examples/site/site.yaml"), root)
    assert root.read_bytes() == first
    with tarfile.open(root) as archive:
        assert archive.getnames() == ["app.json", "edge.json", "site.yaml"]
        assert "whoami" in json.loads(archive.extractfile("app.json").read())["services"]


def test_v2_compile_accepts_rendered_models_without_legacy_inputs(tmp_path):
    output = tmp_path / "bundle.tar"
    compile_bundle(Rendered({"services": {}}, {"services": {}}), Path("examples/site/site.yaml"), output)
    assert output.is_file()
