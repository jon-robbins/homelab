"""Docker daemon checks for the example render and inline config mounts."""

import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import yaml

from scripts.release import compose_policy
from scripts.release.render import render


ROOT = Path(__file__).parents[2]


def _docker(*args, check=True):
    return subprocess.run(["docker", *map(str, args)], check=check, capture_output=True, text=True)


def test_compose_hash_changes_for_inline_config_content(tmp_path):
    platform = tmp_path / "platform"
    module = platform / "modules/app/demo.compose.yml"
    module.parent.mkdir(parents=True)
    module.write_text(yaml.safe_dump({"services": {"demo": {
        "image": "traefik/whoami:v1.10.3@sha256:" + "a" * 64,
        "healthcheck": {"test": ["CMD", "true"]},
        "labels": {"homelab.state": "stateless"},
        "configs": ["settings"],
    }}, "configs": {"settings": {"content": "first\n"}}}))
    site = tmp_path / "site"
    site.mkdir()
    (site / "site.env").write_text(f"APP_DATA_DIR={tmp_path / 'data'}\n")
    (site / "site.yaml").write_text(yaml.safe_dump({
        "contract": 1, "site_id": "docker-test", "plugins": {}, "services": {"demo": {}},
        "managed": [], "bind_roots": ["APP_DATA_DIR"], "marker_roots": [],
    }))

    def write_model(path, model):
        path.write_text(json.dumps({"services": model["services"], "configs": model["configs"]}))

    first = render(platform, site, {}).app
    second = render(platform, site, {}).app
    first_file, second_file = tmp_path / "first.json", tmp_path / "second.json"
    write_model(first_file, first)
    write_model(second_file, second)
    first_hash = _docker("compose", "-f", first_file, "config", "--hash", "demo").stdout
    second_hash = _docker("compose", "-f", second_file, "config", "--hash", "demo").stdout
    assert first_hash == second_hash

    module.write_text(yaml.safe_dump({"services": {"demo": {
        "image": "traefik/whoami:v1.10.3@sha256:" + "a" * 64,
        "healthcheck": {"test": ["CMD", "true"]},
        "labels": {"homelab.state": "stateless"},
        "configs": ["settings"],
    }}, "configs": {"settings": {"content": "second\n"}}}))
    changed = render(platform, site, {}).app
    changed_file = tmp_path / "changed.json"
    write_model(changed_file, changed)
    changed_hash = _docker("compose", "-f", changed_file, "config", "--hash", "demo").stdout
    assert changed_hash != first_hash


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


def test_an_import_through_the_rendered_data_mount_is_a_real_hardlink(tmp_path):
    """One disk: qBittorrent writes under /data/downloads, Sonarr links it into /data/tv. Same inode, no copy."""
    site = tmp_path / "site"
    shutil.copytree(ROOT / "tests/modules/site", site)
    media = tmp_path / "media"
    (media / "downloads").mkdir(parents=True)
    (media / "tv").mkdir()
    env = (site / "site.env").read_text().replace("/tmp/homelab-catalog/media", str(media))
    (site / "site.env").write_text(env)
    services = render(ROOT, site, {}).app["services"]

    def mounts(name):
        return [
            arg
            for volume in services[name]["volumes"]
            if volume["target"] in ("/data", "/data/downloads")
            for arg in ("-v", f"{volume['source']}:{volume['target']}")
        ]

    image = "alpine:3.20"
    _docker("run", "--rm", *mounts("qbittorrent"), image, "sh", "-c", "echo episode > /data/downloads/show.mkv")
    _docker("run", "--rm", *mounts("sonarr"), "--entrypoint", "ln", services["sonarr"]["image"],
            "/data/downloads/show.mkv", "/data/tv/show.mkv")
    assert (media / "tv/show.mkv").stat().st_ino == (media / "downloads/show.mkv").stat().st_ino
    assert (media / "tv/show.mkv").stat().st_nlink == 2


_GOOD_ROUTES = "http:\n  routers:\n    r: {rule: 'PathPrefix(`/x`)', service: s}\n  services:\n    s: {loadBalancer: {servers: [{url: 'http://127.0.0.1:1'}]}}\n"


def _traefik_edge(tmp_path: Path, routes: str) -> tuple[tuple, str]:
    """Render the traefik fixture site against a scratch data dir; return the compose command and project."""
    site = tmp_path / "site"
    shutil.copytree(ROOT / "tests/modules/site_traefik", site)
    (site / "edge/traefik.yml").write_text(routes)
    data, secrets = tmp_path / "data", tmp_path / "secrets"
    (data / "traefik/acme").mkdir(parents=True)
    (data / "traefik/acme").chmod(0o777)  # the container runs as PUID:PGID, not as the test user
    secrets.mkdir()
    (secrets / "CLOUDFLARE_TOKEN").write_text("not-a-real-token\n")
    env = [line for line in (site / "site.env").read_text().splitlines()
           if line.split("=")[0] not in ("APP_DATA_DIR", "HOMELAB_SECRET_DIR")]
    (site / "site.env").write_text("\n".join([*env, f"APP_DATA_DIR={data}", f"HOMELAB_SECRET_DIR={secrets}"]) + "\n")
    model = render(ROOT, site, {}).edge
    service = {k: v for k, v in model["services"]["traefik"].items() if k not in ("ports", "extra_hosts")}
    service["networks"] = {"n": None}
    compose = tmp_path / "edge.json"
    compose.write_text(json.dumps({"services": {"traefik": service}, "configs": model["configs"],
                                   "secrets": model["secrets"], "networks": {"n": {}}}))
    project = "homelab-traefik-" + uuid.uuid4().hex[:8]
    return ("compose", "-p", project, "-f", compose), service["healthcheck"]["test"][1:]


def _wait(predicate, seconds: int = 60) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(1)
    return False


def test_traefik_module_is_healthy_hardened_and_unhealthy_when_routes_do_not_load(tmp_path):
    for routes, healthy in ((_GOOD_ROUTES, True), ("http:\n  routers: [unclosed\n", False)):
        run = tmp_path / ("good" if healthy else "broken")
        run.mkdir()
        command, health = _traefik_edge(run, routes)
        try:
            _docker(*command, "up", "-d")
            container = _docker(*command, "ps", "-q", "traefik").stdout.strip()
            probe = lambda: _docker("exec", container, *health, check=False).returncode == 0  # noqa: E731
            if healthy:
                assert _wait(probe), _docker("logs", container).stderr
                caps = _docker("exec", container, "sh", "-c", "id -u; grep -E '^(CapEff|NoNewPrivs)' /proc/self/status").stdout
                assert caps.split() == ["1000", "CapEff:", "0000000000000000", "NoNewPrivs:", "1"]
                assert (run / "data/traefik/acme/acme.json").stat().st_mode & 0o777 == 0o600
            else:
                # Traefik stays up when the dynamic files fail to load; only /healthz tells the updater.
                def failed() -> bool:
                    logs = _docker("logs", container)
                    return "Cannot start the provider" in logs.stdout + logs.stderr

                assert _wait(failed)
                assert not probe()
        finally:
            _docker(*command, "down", "-v", check=False)
