import json
from pathlib import Path

import pytest

from scripts.controller.planner import ContainerView, Plan, compose_commands, plan


class Adapter:
    def __init__(self, hashes, containers, exists=()):
        self.hashes = hashes
        self.containers = containers
        self.exists = set(exists)

    def config_hash(self, release_dir, project, service):
        return self.hashes[(project, service)]

    def container(self, project, service):
        return self.containers.get((project, service))

    def path_exists(self, path):
        return str(path) in self.exists


def release(tmp_path: Path, app, edge, site):
    (tmp_path / "app.json").write_text(json.dumps({"services": app}))
    (tmp_path / "edge.json").write_text(json.dumps({"services": edge}))
    (tmp_path / "site.yaml").write_text(site)


@pytest.mark.parametrize(
    "app,edge,site,hashes,containers,exists,expected",
    [
        pytest.param(
            {"changed": {}, "missing": {}},
            {},
            "managed: [changed, missing]\nservices:\n  missing: {state: stopped}\n",
            {("homelab", "changed"): "new", ("homelab", "missing"): "new"},
            {("homelab", "changed"): ContainerView("old", "running", "")},
            (),
            Plan(("changed",), ("missing",), (), ()),
            id="managed changed-or-missing recreates-or-creates-stopped",
        ),
        pytest.param(
            {"same": {}},
            {},
            "managed: [same]\nservices: {}\n",
            {("homelab", "same"): "same"},
            {("homelab", "same"): ContainerView("same", "running", "")},
            (),
            Plan((), (), (), ()),
            id="matching-hash does-nothing",
        ),
        pytest.param(
            {"unmanaged": {}},
            {"proxy": {}},
            "managed: []\nservices: {}\n",
            {("homelab-edge", "proxy"): "new"},
            {("homelab-edge", "proxy"): ContainerView("old", "running", "")},
            (),
            Plan((), (), ("edge change pending: proxy",), ()),
            id="unmanaged-and-edge-are-not-recreated",
        ),
        pytest.param(
            {"owned": {}},
            {},
            "managed: [owned]\nservices: {}\n",
            {("homelab", "owned"): "new"},
            {("homelab", "owned"): ContainerView("old", "exited", "")},
            (),
            Plan((), (), ("stopped by owner: owned",), ()),
            id="owner-stopped-service-is-not-recreated",
        ),
        pytest.param(
            {"broken": {}},
            {},
            "managed: [broken]\nservices: {}\n",
            {("homelab", "broken"): "new"},
            {("homelab", "broken"): ContainerView("old", "running", "boom")},
            (),
            Plan((), (), (), ("broken failed to start: boom",)),
            id="start-error-is-a-failure",
        ),
        pytest.param(
            {"marked": {"volumes": [{"type": "bind", "source": "/media/.homelab-mounted"}]}},
            {},
            "managed: []\nservices: {}\n",
            {},
            {},
            (),
            Plan((), (), (), ("media disk missing: /media/.homelab-mounted",)),
            id="missing-marker-is-a-failure",
        ),
    ],
)
def test_planning_rules(tmp_path, app, edge, site, hashes, containers, exists, expected):
    release(tmp_path, app, edge, site)
    assert plan(tmp_path, Adapter(hashes, containers, exists)) == expected


def test_compose_commands_are_empty_or_explicit(tmp_path):
    assert compose_commands(Plan((), (), (), ()), tmp_path) == []
    commands = compose_commands(Plan(("a",), ("b",), (), ()), tmp_path)
    assert commands == [
        [
            "docker",
            "compose",
            "-p",
            "homelab",
            "-f",
            str(tmp_path / "app.json"),
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--pull",
            "never",
            "a",
        ],
        ["docker", "compose", "-p", "homelab", "-f", str(tmp_path / "app.json"), "up", "--no-deps", "--no-start", "b"],
    ]
