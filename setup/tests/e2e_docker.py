"""Real Docker: render the example site, apply it locally, and check what a person would rely on.

Brings up and removes the "homelab" Compose project and the homelab_net network, so it refuses to run against a
daemon that already has either (nox session `wizard_e2e`).
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from setup import cli

ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def host_preparation_is_outside_engine_e2e(monkeypatch):
    """These tests exercise the engine against Docker, not privileged host preparation."""
    monkeypatch.setattr(cli, "preflight", lambda **kwargs: None)
    monkeypatch.setattr(cli, "install_pinned_packages", lambda: None)
    monkeypatch.setattr(cli, "ensure_service_user", lambda: None)
    monkeypatch.setattr(cli, "apply_local_host_config", lambda: None)


def docker(*argv: str) -> str:
    return subprocess.run(["docker", *argv], check=True, capture_output=True, text=True).stdout.strip()


def container_state() -> dict:
    ids = docker("ps", "-aq", "--filter", "label=com.docker.compose.project=homelab").split()
    return {i: json.loads(docker("inspect", i))[0]["State"]["Status"] for i in ids}


@pytest.fixture
def local_site(tmp_path):
    if (
        container_state()
        or docker("ps", "-aq", "--filter", "label=com.docker.compose.project=homelab-edge")
        or docker("network", "ls", "-q", "--filter", "name=^homelab_net$")
    ):
        pytest.fail("this Docker daemon already has a 'homelab' project or homelab_net; refusing to run")
    site = tmp_path / "site"
    shutil.copytree(ROOT / "examples/site", site)
    shutil.copytree(ROOT / "examples/plugin-hello", tmp_path / "plugin-hello")
    env = (site / "site.env").read_text().replace("/tmp/homelab-example", str(tmp_path / "host"))
    (site / "site.env").write_text(env)
    for name in ("data", "media-hdd", "media-nvme", "media-plex"):
        root = tmp_path / "host" / name
        root.mkdir(parents=True)
        (root / ".homelab-mounted").touch()
    yield ["--root", str(tmp_path / "srv"), "--site", str(site), "--platform", str(ROOT)]
    for model in (tmp_path / "srv/setup/releases").glob("*/app.json"):
        subprocess.run(
            ["docker", "compose", "-p", "homelab", "-f", str(model), "down", "-v", "--remove-orphans"],
            check=True,
            capture_output=True,
        )
    subprocess.run(["docker", "network", "rm", "homelab_net"], capture_output=True)


def test_apply_starts_the_stack_and_a_second_apply_leaves_it_alone(local_site, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_ops", lambda: cli.LocalOps(settle_s=2))
    assert cli.main([*local_site, "apply"]) == 0
    first = container_state()
    assert list(first.values()) == ["running"]
    assert cli.main([*local_site, "status"]) == 0
    assert cli.main([*local_site, "apply"]) == 0
    assert container_state() == first  # same container, not recreated


def test_a_service_stopped_by_its_owner_is_reported_and_left_stopped(local_site, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_ops", lambda: cli.LocalOps(settle_s=2))
    assert cli.main([*local_site, "apply"]) == 0
    (identifier,) = container_state()
    docker("stop", identifier)
    assert cli.main([*local_site, "apply"]) == 1
    assert "stopped" in capsys.readouterr().err
    assert list(container_state().values()) == ["exited"]


def test_failed_healthcheck_stops_apply(local_site, monkeypatch, capsys):
    site = Path(local_site[local_site.index("--site") + 1])
    (site / "overrides").mkdir(exist_ok=True)
    (site / "overrides/whoami.yml").write_text(
        'services:\n  whoami:\n    healthcheck:\n      test: ["CMD", "/does-not-exist"]\n      interval: 1s\n      timeout: 1s\n      retries: 1\n'
    )
    monkeypatch.setattr(cli, "make_ops", lambda: cli.LocalOps(settle_s=2))
    assert cli.main([*local_site, "apply"]) == 1
    assert "unhealthy" in capsys.readouterr().err


def test_interrupt_after_compose_up_resumes_without_recreating(local_site, monkeypatch):
    ops = cli.LocalOps(settle_s=2)
    original = ops.run

    def interrupted(argv):
        original(argv)
        if "up" in argv:
            raise KeyboardInterrupt

    ops.run = interrupted
    monkeypatch.setattr(cli, "make_ops", lambda: ops)
    assert cli.main([*local_site, "apply"]) == 130
    first = container_state()
    assert first
    monkeypatch.setattr(cli, "make_ops", lambda: cli.LocalOps(settle_s=2))
    assert cli.main([*local_site, "apply"]) == 0
    assert container_state() == first
