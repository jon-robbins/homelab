import json
from pathlib import Path

from scripts.controller.planner import ContainerView, Plan, compose_commands, plan


class Adapter:
    def __init__(self, hashes, containers, exists=(), version="2"):
        self.hashes = hashes
        self.containers = containers
        self.exists = set(exists)
        self.version = version

    def config_hash(self, release_dir, project, service):
        return self.hashes[service]

    def container(self, project, service):
        return self.containers.get(service)

    def compose_version(self):
        return self.version

    def path_exists(self, path):
        return str(path) in self.exists


def release(tmp_path: Path, app, edge, site):
    (tmp_path / "app.json").write_text(json.dumps({"services": app}))
    (tmp_path / "edge.json").write_text(json.dumps({"services": edge}))
    (tmp_path / "site.yaml").write_text(site)


def test_plans_managed_changes_and_read_only_checks(tmp_path):
    release(tmp_path, {
        "new": {"volumes": [{"type": "bind", "source": "/media/.homelab-mounted"}]},
        "stopped": {}, "same": {}, "owned": {}, "broken": {}, "unmanaged": {},
    }, {"proxy": {}}, "managed: [new, stopped, same, owned, broken]\nservices:\n  stopped: {state: stopped}\n")
    adapter = Adapter(
        {"new": "new", "stopped": "stopped-new", "same": "same", "owned": "owned",
         "broken": "broken-new", "proxy": "edge-new"},
        {"stopped": ContainerView("old", "running", ""), "same": ContainerView("same", "running", ""),
         "owned": ContainerView("owned", "exited", ""), "broken": ContainerView("old", "exited", "boom"),
         "proxy": ContainerView("old", "running", "")},
        exists=())
    assert plan(tmp_path, adapter, None) == Plan(
        ("new",), ("stopped",), ("stopped by owner: owned", "edge change pending: proxy"),
        ("media disk missing: /media/.homelab-mounted", "broken failed to start: boom"), None)


def test_compose_version_hold_does_not_suppress_plan(tmp_path):
    release(tmp_path, {"app": {}}, {}, "managed: [app]\nservices: {}\n")
    result = plan(tmp_path, Adapter({"app": "new"}, {}, version="3"), "2")
    assert result.recreate == ("app",)
    assert result.hold == "Compose changed: check homelabctl status, then reconcile --now"


def test_compose_commands_are_empty_or_explicit(tmp_path):
    assert compose_commands(Plan((), (), (), (), None), tmp_path) == []
    commands = compose_commands(Plan(("a",), ("b",), (), (), None), tmp_path)
    assert commands == [
        ["docker", "compose", "-p", "homelab", "-f", str(tmp_path / "app.json"), "up", "-d",
         "--no-deps", "--no-build", "--pull", "never", "a"],
        ["docker", "compose", "-p", "homelab", "-f", str(tmp_path / "app.json"), "up",
         "--no-deps", "--no-start", "b"],
    ]
