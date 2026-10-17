import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.release.contracts import ValidationError
from scripts.release.render import render


IMAGE = "example/test@sha256:" + "a" * 64
ROOT = Path(__file__).parents[2]


def _module(root: Path, name="demo", tier="app") -> Path:
    path = root / "modules" / tier / f"{name}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"services": {name: {
        "image": IMAGE, "volumes": [{"type": "bind", "source": "${APP_DATA_DIR}/data", "target": "/data"}],
        "healthcheck": {"test": ["CMD", "true"]}, "labels": {"homelab.state": "stateless"},
        "configs": [{"source": "settings", "target": "/etc/settings"}],
    }}, "configs": {"settings": {"file": "site:settings.ini"}}}), encoding="utf-8")
    return path


def _site(tmp_path: Path, service="demo", source=None) -> Path:
    site = tmp_path / "site"
    site.mkdir()
    (site / "site.env").write_text("APP_DATA_DIR=/tmp/homelab-render-data\n", encoding="utf-8")
    document = {"contract": 1, "site_id": "test", "plugins": {}, "services": {service: {}},
                "managed": [service], "bind_roots": ["APP_DATA_DIR"], "marker_roots": ["APP_DATA_DIR"]}
    if source:
        document["plugins"] = {source: {"repository": "owner/plugin"}}
        document["services"][service] = {"from": source}
    (site / "site.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    (site / "settings.ini").write_text("hello\n", encoding="utf-8")
    return site


def test_render_inlines_config_copies_mode_and_adds_one_marker(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    _module(platform)
    site = _site(tmp_path)
    seen = {}

    def compose(argv, **kwargs):
        seen["argv"] = argv
        files = [Path(argv[index + 1]) for index, value in enumerate(argv[:-1]) if value == "-f"]
        document = yaml.safe_load(files[0].read_text())
        service = document["services"]["demo"]
        assert document["configs"]["settings"]["content"] == "hello\n"
        assert service["configs"][0]["mode"] == (site / "settings.ini").stat().st_mode & 0o777
        assert sum(volume.get("target") == "/homelab-markers/APP_DATA_DIR" for volume in service["volumes"]) == 1
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({"services": {
            "demo": {"volumes": [{"type": "bind", "source": "/tmp/homelab-render-data/data",
                                     "target": "/data", "bind": {"create_host_path": False}}]}}}),
            "stderr": ""})()

    monkeypatch.setattr("scripts.release.render.subprocess.run", compose)
    result = render(platform, site, {})
    assert result.edge == {}
    assert seen["argv"][-5:] == ["--env-file", str(site / "site.env"), "config", "--format", "json"]


def test_render_selects_plugin_module(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    plugin = tmp_path / "plugin"
    _module(plugin)
    (plugin / "homelab-plugin.yaml").write_text("contract: 1\nname: plugin\ndescription: test\n", encoding="utf-8")
    (plugin / "README.md").write_text("demo\n", encoding="utf-8")
    site = _site(tmp_path, source="plugin")

    def compose(argv, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": '{"services": {"demo": {}}}', "stderr": ""})()

    monkeypatch.setattr("scripts.release.render.subprocess.run", compose)
    render(platform, site, {"plugin": plugin})


def test_render_rejects_missing_module(tmp_path):
    with pytest.raises(ValidationError, match="exactly one module"):
        render(tmp_path / "platform", _site(tmp_path), {})


def test_render_adds_marker_for_short_bind_and_copies_mode_for_short_config(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    module = _module(platform)
    document = yaml.safe_load(module.read_text())
    document["services"]["demo"]["volumes"] = ["${APP_DATA_DIR}/data:/data:ro"]
    document["services"]["demo"]["configs"] = ["settings:/etc/settings"]
    module.write_text(yaml.safe_dump(document), encoding="utf-8")
    site = _site(tmp_path)

    def compose(argv, **kwargs):
        files = [Path(argv[index + 1]) for index, value in enumerate(argv[:-1]) if value == "-f"]
        rendered = yaml.safe_load(files[0].read_text())
        service = rendered["services"]["demo"]
        assert service["configs"] == [{"source": "settings", "target": "/etc/settings",
                                        "mode": (site / "settings.ini").stat().st_mode & 0o777}]
        assert sum(volume.get("target") == "/homelab-markers/APP_DATA_DIR"
                   for volume in service["volumes"] if isinstance(volume, dict)) == 1
        return type("Result", (), {"returncode": 0, "stdout": '{"services": {"demo": {}}}', "stderr": ""})()

    monkeypatch.setattr("scripts.release.render.subprocess.run", compose)
    render(platform, site, {})


def test_render_copies_mode_for_single_name_config(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    module = _module(platform)
    document = yaml.safe_load(module.read_text())
    document["services"]["demo"]["configs"] = ["settings"]
    module.write_text(yaml.safe_dump(document), encoding="utf-8")
    site = _site(tmp_path)

    def inspect(docs):
        assert docs[0]["services"]["demo"]["configs"] == [{
            "source": "settings", "target": "/settings",
            "mode": (site / "settings.ini").stat().st_mode & 0o777,
        }]

    _fake_compose(monkeypatch, inspect)
    render(platform, site, {})


def test_marker_root_expands_other_site_env_variables(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    module = _module(platform)
    document = yaml.safe_load(module.read_text())
    document["services"]["demo"]["volumes"] = [{
        "type": "bind", "source": "${APP_DATA_DIR}/media/movie", "target": "/media",
    }]
    module.write_text(yaml.safe_dump(document), encoding="utf-8")
    site = _site(tmp_path)
    site_document = yaml.safe_load((site / "site.yaml").read_text())
    site_document["bind_roots"] = ["APP_DATA_DIR", "MEDIA_HDD_PATH"]
    site_document["marker_roots"] = ["MEDIA_HDD_PATH"]
    (site / "site.yaml").write_text(yaml.safe_dump(site_document), encoding="utf-8")
    (site / "site.env").write_text(
        "APP_DATA_DIR=/tmp/data\nMEDIA_HDD_PATH=/tmp/data/media\n", encoding="utf-8"
    )

    def inspect(docs):
        volumes = docs[0]["services"]["demo"]["volumes"]
        assert sum(v.get("target") == "/homelab-markers/MEDIA_HDD_PATH" for v in volumes) == 1

    _fake_compose(monkeypatch, inspect)
    render(platform, site, {})


def test_render_rejects_unlisted_plugin_platform_collision(tmp_path):
    platform = tmp_path / "platform"
    plugin = tmp_path / "plugin"
    _module(platform)
    _module(plugin)
    (plugin / "homelab-plugin.yaml").write_text("contract: 1\nname: plugin\ndescription: test\n")
    (plugin / "README.md").write_text("demo\n")
    site = _site(tmp_path, source="plugin")
    site_document = yaml.safe_load((site / "site.yaml").read_text())
    site_document["plugins"] = {}
    (site / "site.yaml").write_text(yaml.safe_dump(site_document), encoding="utf-8")
    with pytest.raises(ValidationError, match="collides with platform module"):
        render(platform, site, {"plugin": plugin})


def _fake_compose(monkeypatch, inspect=None, model=None):
    calls = []

    def compose(argv, **kwargs):
        files = [Path(argv[index + 1]) for index, value in enumerate(argv[:-1]) if value == "-f"]
        docs = [yaml.safe_load(path.read_text()) for path in files]
        calls.append((argv, kwargs, docs))
        if inspect:
            inspect(docs)
        output = model if model is not None else {"services": {"demo": {}}}
        return type("Result", (), {"returncode": 0, "stdout": json.dumps(output), "stderr": ""})()

    monkeypatch.setattr("scripts.release.render.subprocess.run", compose)
    return calls


def test_platform_module_selected_and_compose_environment_is_path_only(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    _module(platform)
    site = _site(tmp_path)
    calls = _fake_compose(monkeypatch)
    render(platform, Path(os.path.relpath(site)), {})
    argv, kwargs, docs = calls[0]
    assert docs[0]["services"]["demo"]["image"] == IMAGE
    assert argv[-5:] == ["--env-file", str(site.resolve() / "site.env"), "config", "--format", "json"]
    assert kwargs["env"] == {"PATH": os.environ["PATH"]}
    assert kwargs["cwd"] != site


def test_platform_plugin_collision_rejected(tmp_path):
    platform = tmp_path / "platform"
    plugin = tmp_path / "plugin"
    _module(platform)
    _module(plugin)
    (plugin / "homelab-plugin.yaml").write_text("contract: 1\nname: plugin\ndescription: test\n")
    (plugin / "README.md").write_text("demo\n")
    with pytest.raises(ValidationError, match="collides with platform module"):
        render(platform, _site(tmp_path, source="plugin"), {"plugin": plugin})


def test_override_order_marker_and_module_config_mode(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    module = _module(platform)
    document = yaml.safe_load(module.read_text())
    document["services"]["demo"].pop("volumes")
    document["services"]["demo"].pop("configs")
    module.write_text(yaml.safe_dump(document))
    site = _site(tmp_path)
    overrides = site / "overrides"
    overrides.mkdir()
    (overrides / "demo.yml").write_text(yaml.safe_dump({"services": {"demo": {
        "volumes": [{"type": "bind", "source": "${APP_DATA_DIR}/other", "target": "/other"}],
        "configs": [{"source": "settings", "target": "/etc/settings"}],
    }}}))

    def inspect(docs):
        assert len(docs) == 2
        assert "configs" in docs[0]
        service = docs[1]["services"]["demo"]
        assert service["configs"][0]["mode"] == (site / "settings.ini").stat().st_mode & 0o777
        assert [v["target"] for v in service["volumes"]] == ["/other", "/homelab-markers/APP_DATA_DIR"]

    calls = _fake_compose(monkeypatch, inspect)
    render(platform, site, {})
    argv = calls[0][0]
    files = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-f"]
    assert "override" in files[1]
    assert "override" not in files[0]


def test_replaced_bind_outside_marker_root_adds_no_marker(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    _module(platform)
    site = _site(tmp_path)
    document = yaml.safe_load((site / "site.yaml").read_text())
    document["bind_roots"].append("MEDIA_HDD_PATH")
    (site / "site.yaml").write_text(yaml.safe_dump(document))
    with (site / "site.env").open("a") as stream:
        stream.write("MEDIA_HDD_PATH=/tmp/homelab-render-media\n")
    overrides = site / "overrides"
    overrides.mkdir()
    (overrides / "demo.yml").write_text(yaml.safe_dump({"services": {"demo": {
        "volumes": [{"type": "bind", "source": "${MEDIA_HDD_PATH}/data", "target": "/data"}],
    }}}))

    def inspect(docs):
        assert len(docs) == 2
        assert all(v.get("target") != "/homelab-markers/APP_DATA_DIR"
                   for doc in docs for v in doc["services"]["demo"].get("volumes", []))

    _fake_compose(monkeypatch, inspect)
    render(platform, site, {})


def test_plugin_relative_config_is_inlined(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    module = _module(plugin)
    document = yaml.safe_load(module.read_text())
    document["configs"]["settings"]["file"] = "settings.ini"
    module.write_text(yaml.safe_dump(document))
    (plugin / "settings.ini").write_text("plugin settings\n")
    (plugin / "homelab-plugin.yaml").write_text("contract: 1\nname: plugin\ndescription: test\n")
    (plugin / "README.md").write_text("demo\n")
    site = _site(tmp_path, source="plugin")
    _fake_compose(monkeypatch, lambda docs: assert_plugin_config(docs))

    def assert_plugin_config(docs):
        assert docs[0]["configs"]["settings"] == {"content": "plugin settings\n"}

    render(tmp_path / "platform", site, {"plugin": plugin})


def test_secret_file_reference_remains_untouched(tmp_path, monkeypatch):
    platform = tmp_path / "platform"
    module = _module(platform)
    document = yaml.safe_load(module.read_text())
    document["secrets"] = {"token": {"file": "${HOMELAB_SECRET_DIR}/token"}}
    module.write_text(yaml.safe_dump(document))
    site = _site(tmp_path)
    with (site / "site.env").open("a") as stream:
        stream.write("HOMELAB_SECRET_DIR=/tmp/homelab-render-secret\n")
    _fake_compose(monkeypatch, lambda docs: assert_secret(docs),
                  {"services": {"demo": {}}, "secrets": {"token": {"file": "/tmp/homelab-render-secret/token"}}})

    def assert_secret(docs):
        assert docs[0]["secrets"] == {"token": {"file": "${HOMELAB_SECRET_DIR}/token"}}

    render(platform, site, {})


@pytest.mark.parametrize("bad", [False, True])
def test_render_site_cli_json_and_policy_failure(tmp_path, bad):
    docker_bin = tmp_path / "bin"
    docker_bin.mkdir()
    model = {"services": {"whoami": {"privileged": True} if bad else {}}}
    fake = docker_bin / "docker"
    fake.write_text("#!/usr/bin/env python3\nimport json\nprint(" + repr(json.dumps(model)) + ")\n")
    fake.chmod(0o755)
    result = subprocess.run([sys.executable, "scripts/dev/render-site", "examples/site",
                             "--plugin", "plugin-hello=examples/plugin-hello"], cwd=ROOT,
                            env={**os.environ, "PATH": f"{docker_bin}:{os.environ['PATH']}"},
                            capture_output=True, text=True)
    assert result.returncode == (1 if bad else 0)
    if bad:
        assert "privileged is not permitted" in result.stderr
    else:
        assert json.loads(result.stdout) == {"app": model, "edge": {}}
