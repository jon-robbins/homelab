"""A fake Docker world: commands go in, container states come out, and every command is recorded."""

import re
from pathlib import Path

import pytest

from scripts.controller.planner import ContainerView
from setup.engine.checkpoints import Checkpoints
from setup.engine.contract import Context
from setup.engine.layout import Layout
from setup.engine.ops import Verdict
from setup.engine.secrets import SecretStore


class FakeOps:
    def __init__(self):
        self.containers: dict[tuple[str, str], str] = {}  # (project, service) -> running | exited
        self.calls: list[list[str]] = []
        self.pulled: list[tuple[str, tuple[str, ...]]] = []
        self.network = False
        self.network_plan = False
        self.broken: set[str] = set()  # services that start but never become healthy
        self.foreign: list[str] = []  # containers in the owner's projects that setup did not create
        self.errors: set[str] = set()  # services whose container failed to start (State.Error)
        self.starting: set[str] = set()  # services whose health check is still starting when first asked
        self.backups = True  # is homelab-backup installed
        self.events: list[tuple[str, str]] = []  # stop and snapshot, in order

    def config_hash(self, release_dir, project, service):
        return f"{project}/{service}"

    def container(self, project, service):
        state = self.containers.get((project, service))
        return ContainerView(f"{project}/{service}", state, "boom" if service in self.errors else "") if state else None

    def inspect(self, service, project="homelab"):
        state = self.containers.get((project, service))
        return {"State": {"Status": state}} if state else None

    def path_exists(self, path):
        return Path(path).exists()

    def pull(self, release_dir, services, tier="app"):
        self.pulled.append((tier, tuple(services)))

    def run(self, argv):
        self.calls.append(argv)
        project = argv[argv.index("-p") + 1]
        service = argv[-1]
        if "up" in argv:
            self.containers[(project, service)] = "created" if "--no-start" in argv else "running"
            self.errors.discard(service)

    def can_snapshot(self):
        return self.backups

    def stop(self, service):
        self.events.append(("stop", service))
        self.containers[("homelab", service)] = "exited"

    def snapshot(self, service, tag):
        self.events.append(("snapshot", service))
        return "snapshot-id"

    def network_exists(self, name):
        return self.network

    def network_ready(self, name, state_dir):
        return self.network and self.network_plan

    def plan_network(self, state_dir):
        return self.network_spec(state_dir)

    def network_spec(self, state_dir):
        return {
            "subnet": "test-subnet",
            "gateway": "test-gateway",
            "caddy_address": "test-caddy",
            "ip_range": "test-range",
        }

    def ensure_network(self, name, state_dir, spec):
        self.network = True
        self.network_plan = True

    def wait_healthy(self, project, service, timeout_s):
        state = self.containers.get((project, service))
        if state != "running":
            return Verdict("fail", f"{service}: {state or 'missing'}")
        if service in self.starting and timeout_s == 0:
            return Verdict("fail", f"{service}: health check still starting")
        return Verdict("fail", f"{service}: unhealthy") if service in self.broken else Verdict("ok", "")

    def foreign_containers(self, releases):
        return self.foreign

    def ups(self):
        return [(c[c.index("-p") + 1], c[-1]) for c in self.calls if "up" in c and "--no-start" not in c]


@pytest.fixture
def ops():
    return FakeOps()


@pytest.fixture
def world(tmp_path):
    return {
        "data": tmp_path / "data",
        "secrets": tmp_path / "secrets",
        "root": tmp_path / "srv",
    }


@pytest.fixture
def ctx(tmp_path, world):
    layout = Layout(world["root"], owner_root=tmp_path / "owner", unit_dir=tmp_path / "units")
    layout.claim()
    return Context(
        layout=layout,
        secrets=SecretStore(world["secrets"]),
        checkpoints=Checkpoints(layout.setup_dir),
        platform=tmp_path,
        site=tmp_path / "site",
    )


def bind(source, target, marker=False):
    volume = {"type": "bind", "source": str(source), "target": target}
    if marker:
        volume.update(read_only=True, bind={"create_host_path": False})
    return volume


@pytest.fixture
def models(world):
    data, secrets = world["data"], world["secrets"]
    app = {
        "services": {
            "db": {
                "image": "db@sha256:" + "a" * 64,
                "labels": {"homelab.state": "stateful"},
                "secrets": [{"source": "DB_PASSWORD"}],
                "volumes": [
                    bind(data / "db", "/var/lib/db"),
                    bind(data / ".homelab-mounted", "/m", marker=True),
                ],
            },
            "web": {
                "image": "web@sha256:" + "b" * 64,
                "secrets": [{"source": "WEB_KEY"}],
            },
            "worker": {"image": "worker@sha256:" + "c" * 64},
        },
        "secrets": {
            "DB_PASSWORD": {"file": str(secrets / "DB_PASSWORD")},
            "WEB_KEY": {"file": str(secrets / "WEB_KEY")},
        },
    }
    edge = {"services": {"caddy": {"image": "caddy@sha256:" + "d" * 64}}}
    site = {
        "managed": ["db", "web", "worker", "caddy"],
        "services": {"db": {}, "web": {}, "worker": {}, "caddy": {}},
    }
    return app, edge, site


def argv_text(calls):
    return [re.sub(r"\s+", " ", " ".join(c)) for c in calls]


@pytest.fixture(autouse=True)
def isolated_host(tmp_path, monkeypatch):
    from setup.engine import layout

    monkeypatch.setattr(layout, "OWNER_ROOT", tmp_path / "owner")
    monkeypatch.setattr(layout, "UNIT_DIR", tmp_path / "units")
