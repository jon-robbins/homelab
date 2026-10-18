import json
from pathlib import Path

import pytest

from scripts.migration.shadow import shadow


FIXTURE = Path(__file__).parent / "fixtures/shadow/base.json"


class Docker:
    def __init__(self, running, candidate=None, fail=False):
        self.inspect_data = {"run": running, "candidate": candidate or running}
        self.calls = []
        self.fail = fail

    def inspect(self, service, project):
        return self.inspect_data["run" if project == "homelab" else "candidate"]

    def run(self, args):
        self.calls.append(args)
        if self.fail and "--no-start" in args:
            raise RuntimeError("create failed")


def release(tmp_path):
    service = {"image": "example/svc", "container_name": "named"}
    for name in ("app", "edge"):
        (tmp_path / f"{name}.json").write_text(json.dumps({"services": {"svc": service} if name == "app" else {}}))


@pytest.mark.parametrize(
    "change, text",
    [
        (lambda x: x, "svc: clean"),
        (lambda x: x["HostConfig"].pop("DeviceRequests"), "HostConfig.DeviceRequests"),
        (lambda x: x["HostConfig"]["CapAdd"].pop(), "HostConfig.CapAdd"),
        (lambda x: x["Config"]["Env"].__setitem__(0, "TOKEN=other"), "Config.Env.TOKEN"),
        (lambda x: x["Mounts"].__setitem__(0, {**x["Mounts"][0], "Source": "/srv/homelab/data/x"}), "svc: clean"),
    ],
    ids=["identical", "device-request", "capability", "environment", "legacy-bind"],
)
def test_shadow_diffs_normalized_inspects(tmp_path, capsys, change, text):
    release(tmp_path)
    running = json.loads(FIXTURE.read_text())
    candidate = json.loads(FIXTURE.read_text())
    change(candidate)
    assert shadow(tmp_path, Path("/legacy"), adapter=Docker(running, candidate)) == (0 if text == "svc: clean" else 1)
    output = capsys.readouterr().out
    assert text in output
    if "environment" in text or "TOKEN" in output:
        assert "other" not in output and "secret-value" not in output


def test_shadow_not_running_is_difference(tmp_path, capsys):
    release(tmp_path)
    assert shadow(tmp_path, Path("/legacy"), adapter=Docker(None)) == 1
    assert capsys.readouterr().out == "svc: not running\n"


def test_shadow_reports_and_cleans_up_when_create_fails(tmp_path, capsys):
    release(tmp_path)
    docker = Docker(json.loads(FIXTURE.read_text()), fail=True)
    assert shadow(tmp_path, Path("/legacy"), adapter=docker) == 1
    assert capsys.readouterr().out == "svc: create failed: create failed\n"
    assert docker.calls and docker.calls[-1][-2:] == ["-f", "svc"]


def test_shadow_drops_disk_markers_and_maps_legacy_binds(tmp_path, capsys):
    marker = {"type": "bind", "source": "/mnt/hdd/.homelab-mounted", "target": "/homelab-markers/MEDIA_HDD_PATH"}
    data = {"type": "bind", "source": "/srv/homelab/data/svc", "target": "/config"}
    service = {"image": "example/svc", "volumes": [data, marker]}
    for name in ("app", "edge"):
        (tmp_path / f"{name}.json").write_text(json.dumps({"services": {"svc": service} if name == "app" else {}}))
    running = json.loads(FIXTURE.read_text())
    candidate = json.loads(FIXTURE.read_text())
    running["HostConfig"]["Binds"] = ["/legacy/svc:/config:rw"]
    candidate["HostConfig"]["Binds"] = ["/srv/homelab/data/svc:/config:rw"]
    docker = Docker(running, candidate)
    written = []
    docker.run = lambda args: written.append(json.loads(Path(args[5]).read_text())) if "--no-start" in args else None
    assert shadow(tmp_path, Path("/legacy"), adapter=docker) == 0
    assert written[0]["services"]["svc"]["volumes"] == [data]
    assert capsys.readouterr().out == "svc: clean\n"


def test_works_with_a_read_only_release_dir(tmp_path):
    release(tmp_path)
    tmp_path.chmod(0o555)
    try:
        assert shadow(tmp_path, tmp_path / "legacy", ["svc"], Docker(json.loads(FIXTURE.read_text()))) == 0
    finally:
        tmp_path.chmod(0o755)


def test_container_lookup_includes_created_containers(monkeypatch):
    import scripts.controller.reconcile as controller

    seen = []
    monkeypatch.setattr(controller.subprocess, "check_output", lambda args, **kw: seen.append(args) or "abc\n")
    assert controller.SubprocessOps()._container_id("homelab-shadow", "svc") == "abc"
    assert "-a" in seen[0]
