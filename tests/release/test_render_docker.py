"""Docker daemon checks for the example render and inline config mounts."""

import json
from pathlib import Path
import subprocess
import uuid

import yaml

from scripts.release import compose_policy
from scripts.release.render import render


ROOT = Path(__file__).parents[2]


def _docker(*args, check=True):
    return subprocess.run(["docker", *map(str, args)], check=check, capture_output=True, text=True)


def test_example_golden_and_runtime_marker_and_config(tmp_path, monkeypatch):
    example = render(ROOT, ROOT / "examples/site", {"plugin-hello": ROOT / "examples/plugin-hello"})
    assert example.edge == {}
    assert example.app["services"]["whoami"]["labels"]["homelab.state"] == "stateless"
    assert any(v["target"] == "/homelab-markers/APP_DATA_DIR"
               for v in example.app["services"]["whoami"]["volumes"])

    # The runtime fixture needs sleep/cat/stat, so it uses the official traefik image, which isn't in the
    # production image allowlist; allow it for this test only.
    allowlist = compose_policy._allowlist
    monkeypatch.setattr(compose_policy, "_allowlist", lambda name: {"namespaces": ["traefik"]} if name == "image-policy.json" else allowlist(name))
    _docker("pull", "traefik:v3.0")
    image = json.loads(_docker("image", "inspect", "traefik:v3.0").stdout)[0]["RepoDigests"][0]
    platform = tmp_path / "platform"
    module = platform / "modules/app/demo.compose.yml"
    module.parent.mkdir(parents=True)
    module.write_text(yaml.safe_dump({"services": {"demo": {
        "image": image, "command": ["sleep", "300"],
        "healthcheck": {"test": ["CMD", "true"]},
        "labels": {"homelab.state": "stateless"},
        "volumes": [{"type": "bind", "source": "${APP_DATA_DIR}/payload", "target": "/data",
                     "bind": {"create_host_path": False}}],
        "configs": [{"source": "settings", "target": "/etc/test-config"}],
    }}, "configs": {"settings": {"file": "site:settings.txt"}}}))
    site = tmp_path / "site"
    site.mkdir()
    data = tmp_path / "data"
    (data / "payload").mkdir(parents=True)
    marker = data / ".homelab-mounted"
    marker.write_text("mounted\n")
    (site / "site.env").write_text(f"APP_DATA_DIR={data}\n")
    (site / "site.yaml").write_text(yaml.safe_dump({
        "contract": 1, "site_id": "docker-test", "plugins": {}, "services": {"demo": {}},
        "managed": ["demo"], "bind_roots": ["APP_DATA_DIR"], "marker_roots": ["APP_DATA_DIR"],
    }))
    config = site / "settings.txt"
    config.write_text("hello from ${HOME} $x inline config\n")
    config.chmod(0o640)
    model = render(platform, site, {}).app
    assert model["services"]["demo"]["labels"]["homelab.state"] == "stateless"
    compose = tmp_path / "rendered.json"
    compose.write_text(json.dumps({"services": model["services"], "configs": model["configs"]}))
    project = "homelab-render-" + uuid.uuid4().hex[:8]
    command = ("compose", "-p", project, "-f", compose)
    try:
        _docker(*command, "up", "-d")
        container = _docker(*command, "ps", "-q", "demo").stdout.strip()
        assert container
        assert _docker("exec", container, "cat", "/etc/test-config").stdout == "hello from ${HOME} $x inline config\n"
        assert _docker("exec", container, "stat", "-c", "%a", "/etc/test-config").stdout.strip() == "640"
        _docker("stop", container)
        marker.unlink()
        started = _docker("start", container, check=False)
        assert started.returncode != 0
        assert "bind source path does not exist" in started.stderr.lower()
    finally:
        _docker(*command, "down", "-v", check=False)
