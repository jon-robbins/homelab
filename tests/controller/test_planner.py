import json
from pathlib import Path

import pytest

from scripts.controller.planner import ContainerView, Plan, compose_commands, plan


class Adapter:
    def __init__(self, hashes, containers, exists=(), version="2"):
        self.hashes = hashes
        self.containers = containers
        self.exists = set(exists)
        self.version = version

    def config_hash(self, release_dir, project, service):
        return self.hashes[(project, service)]

    def container(self, project, service):
        return self.containers.get((project, service))

    def compose_version(self):
        return self.version

    def path_exists(self, path):
        return str(path) in self.exists


def release(tmp_path: Path, app, edge, site):
    (tmp_path / "app.json").write_text(json.dumps({"services": app}))
    (tmp_path / "edge.json").write_text(json.dumps({"services": edge}))
    (tmp_path / "site.yaml").write_text(site)


@pytest.mark.parametrize(
    "app,edge,site,hashes,containers,exists,recorded,version,expected",
    [
        pytest.param(
            {"changed": {}, "missing": {}}, {},
            "managed: [changed, missing]\nservices:\n  missing: {state: stopped}\n",
            {("homelab", "changed"): "new", ("homelab", "missing"): "new"},
            {("homelab", "changed"): ContainerView("old", "running", "")},
            (), None, "2", Plan(("changed",), ("missing",), (), (), None),
            id="managed changed-or-missing recreates-or-creates-stopped",
        ),
        pytest.param(
            {"same": {}}, {}, "managed: [same]\nservices: {}\n",
            {("homelab", "same"): "same"},
            {("homelab", "same"): ContainerView("same", "running", "")},
            (), None, "2", Plan((), (), (), (), None),
            id="matching-hash does-nothing",
        ),
        pytest.param(
            {"unmanaged": {}}, {"proxy": {}}, "managed: []\nservices: {}\n",
            {("homelab-edge", "proxy"): "new"},
            {("homelab-edge", "proxy"): ContainerView("old", "running", "")},
            (), None, "2", Plan((), (), ("edge change pending: proxy",), (), None),
            id="unmanaged-and-edge-are-not-recreated",
        ),
        pytest.param(
            {"owned": {}}, {}, "managed: [owned]\nservices: {}\n",
            {("homelab", "owned"): "new"},
            {("homelab", "owned"): ContainerView("old", "exited", "")},
            (), None, "2", Plan((), (), ("stopped by owner: owned",), (), None),
            id="owner-stopped-service-is-not-recreated",
        ),
        pytest.param(
            {"broken": {}}, {}, "managed: [broken]\nservices: {}\n",
            {("homelab", "broken"): "new"},
            {("homelab", "broken"): ContainerView("old", "running", "boom")},
            (), None, "2", Plan((), (), (), ("broken failed to start: boom",), None),
            id="start-error-is-a-failure",
        ),
        pytest.param(
            {"marked": {"volumes": [{"type": "bind", "source": "/media/.homelab-mounted"}]}},
            {}, "managed: []\nservices: {}\n", {}, {}, (), None, "2",
            Plan((), (), (), ("media disk missing: /media/.homelab-mounted",), None),
            id="missing-marker-is-a-failure",
        ),
        pytest.param(
            {"app": {}}, {}, "managed: [app]\nservices: {}\n",
            {("homelab", "app"): "new"},
            {("homelab", "app"): ContainerView("old", "running", "")},
            (), "2", "3", Plan(("app",), (), (), (),
                                  "Compose changed: check homelabctl status, then reconcile --now"),
            id="compose-version-hold-keeps-plan",
        ),
    ],
)
def test_planning_rules(tmp_path, app, edge, site, hashes, containers, exists,
                       recorded, version, expected):
    release(tmp_path, app, edge, site)
    assert plan(tmp_path, Adapter(hashes, containers, exists, version), recorded) == expected


def test_compose_version_hold_does_not_suppress_plan(tmp_path):
    release(tmp_path, {"app": {}}, {}, "managed: [app]\nservices: {}\n")
    result = plan(tmp_path, Adapter({("homelab", "app"): "new"}, {}, version="3"), "2")
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
