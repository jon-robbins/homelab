"""Render tests against real `docker compose config` (no daemon needed), so normalization isn't faked."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from scripts.release import render as render_module
from scripts.release.contracts import ValidationError
from scripts.release.render import main, render

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode,
    reason="needs the docker CLI with Compose",
)
IMAGE = "traefik/whoami:v1.10.3@sha256:" + "a" * 64
# Render tests need writable-looking paths, so they keep their own small site.env next to the shared fixture site.
ENV = (Path(__file__).parent / "fixtures/render.env").read_text()


def module(root: Path, name="demo", tier="app", **service) -> Path:
    body = {
        "image": IMAGE,
        "healthcheck": {"test": ["CMD", "true"]},
        "labels": {"homelab.state": "stateless"},
        **service,
    }
    document = {"services": {name: body}}
    if "configs" in service:
        document["configs"] = {"settings": {"file": "site:settings.ini"}}
    if "secrets" in service:
        document["secrets"] = {"TOKEN": {"file": "${HOMELAB_SECRET_DIR}/TOKEN"}}
    path = root / "modules" / tier / f"{name}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def site(root: Path, services=None, plugins=None, markers=("MEDIA_HDD_PATH",)) -> Path:
    folder = root / "site"
    folder.mkdir(parents=True)
    config = {
        "contract": 1,
        "site_id": "test",
        "bind_roots": ["APP_DATA_DIR", "MEDIA_HDD_PATH"],
        "marker_roots": list(markers),
        "plugins": plugins or {},
        "services": services or {"demo": {}},
        "managed": [],
    }
    (folder / "site.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (folder / "site.env").write_text(ENV, encoding="utf-8")
    (folder / "settings.ini").write_text("hello\n", encoding="utf-8")
    (folder / "settings.ini").chmod(0o640)
    return folder


def demo(result):
    return result.app["services"]["demo"]


def test_short_syntax_is_normalized_and_a_marker_is_added_once(tmp_path):
    module(tmp_path / "platform", volumes=["${MEDIA_HDD_PATH}/tv:/tv", "${APP_DATA_DIR}/demo:/config"])
    volumes = demo(render(tmp_path / "platform", site(tmp_path), {}))["volumes"]
    markers = [v for v in volumes if v["target"] == "/homelab-markers/MEDIA_HDD_PATH"]
    assert markers == [
        {
            "type": "bind",
            "source": "/tmp/render-test/media/.homelab-mounted",
            "target": "/homelab-markers/MEDIA_HDD_PATH",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
    assert {"source": "/tmp/render-test/media/tv", "target": "/tv"}.items() <= next(
        v for v in volumes if v["target"] == "/tv"
    ).items()


def test_no_marker_without_a_bind_under_a_marker_root(tmp_path):
    module(tmp_path / "platform", volumes=["${APP_DATA_DIR}/demo:/config"])
    assert all(
        "homelab-markers" not in v["target"] for v in demo(render(tmp_path / "platform", site(tmp_path), {}))["volumes"]
    )


@pytest.mark.parametrize("entry", [{"source": "settings", "target": "/etc/settings"}, "settings"])
def test_config_is_inlined_with_the_source_files_mode(tmp_path, entry):
    module(tmp_path / "platform", configs=[entry])
    result = render(tmp_path / "platform", site(tmp_path), {})
    assert result.app["configs"]["settings"]["content"] == "hello\n" and "file" not in result.app["configs"]["settings"]
    assert demo(result)["configs"][0]["mode"] == 0o640


def test_site_override_is_merged_by_compose_and_gets_a_marker(tmp_path):
    module(tmp_path / "platform", volumes=["${APP_DATA_DIR}/demo:/config"])
    folder = site(tmp_path)
    (folder / "overrides").mkdir()
    (folder / "overrides/demo.yml").write_text(
        yaml.safe_dump({"services": {"demo": {"volumes": ["${MEDIA_HDD_PATH}/extra:/extra"]}}})
    )
    targets = {v["target"] for v in demo(render(tmp_path / "platform", folder, {}))["volumes"]}
    assert {"/config", "/extra", "/homelab-markers/MEDIA_HDD_PATH"} <= targets


def test_secret_stays_a_file_reference(tmp_path):
    module(tmp_path / "platform", secrets=["TOKEN"])
    secret = render(tmp_path / "platform", site(tmp_path), {}).app["secrets"]["TOKEN"]
    assert secret["file"] == "/tmp/render-test/secrets/TOKEN" and "content" not in secret


def test_plugin_module_is_selected_and_its_namespace_enforced(tmp_path):
    plugin = tmp_path / "plugin"
    module(plugin, image="ghcr.io/me/plugin/demo" + IMAGE[IMAGE.index("@") :])
    (plugin / "homelab-plugin.yaml").write_text(
        "contract: 1\nname: plugin\ndescription: d\nimage_namespace: ghcr.io/me/plugin\n"
    )
    (plugin / "README.md").write_text("demo")
    folder = site(tmp_path, services={"demo": {"from": "plugin"}}, plugins={"plugin": {"repository": "me/plugin"}})
    assert demo(render(tmp_path / "platform", folder, {"plugin": plugin}))["image"].startswith(
        "ghcr.io/me/plugin/demo@"
    )
    module(plugin)  # the same service, now with an image outside the plugin's namespace
    with pytest.raises(ValidationError, match="namespace is not permitted"):
        render(tmp_path / "platform", folder, {"plugin": plugin})


def test_unknown_plugin_and_missing_module_are_errors(tmp_path):
    (tmp_path / "platform").mkdir()
    with pytest.raises(ValidationError, match="unknown plugin ghost"):
        render(tmp_path / "platform", site(tmp_path, services={"demo": {"from": "ghost"}}), {})
    with pytest.raises(ValidationError, match="no module for this service"):
        render(tmp_path / "platform", site(tmp_path / "again"), {})


def test_compose_runs_with_only_path_in_its_environment(tmp_path, monkeypatch):
    module(tmp_path / "platform")
    seen, real = [], subprocess.run
    monkeypatch.setattr(render_module.subprocess, "run", lambda argv, **kw: seen.append(kw["env"]) or real(argv, **kw))
    render(tmp_path / "platform", site(tmp_path), {})
    assert seen and all(set(env) == {"PATH"} for env in seen)


def test_cli_prints_json_and_fails_on_policy_violations(tmp_path, monkeypatch, capsys):
    module(tmp_path / "platform")
    folder = site(tmp_path)
    monkeypatch.chdir(tmp_path / "platform")
    assert main([str(folder)]) == 0
    assert "demo" in json.loads(capsys.readouterr().out)["app"]["services"]
    module(tmp_path / "platform", privileged=True)
    assert main([str(folder)]) == 1
    assert "privileged is not permitted" in capsys.readouterr().err
