import json
from pathlib import Path

import pytest

from scripts.controller.shadow import shadow


FIXTURE = Path(__file__).parent / "fixtures/shadow/base.json"


class Docker:
    def __init__(self, running, candidate=None, fail=False):
        self.inspect_data = {"run": running, "candidate": candidate or running}
        self.calls = []
        self.fail = fail

    def ids(self, project, service):
        return [] if project == "homelab" and self.inspect_data["run"] is None else [project]

    def inspect(self, identifier):
        return self.inspect_data["run" if identifier == "homelab" else "candidate"]

    def run(self, args):
        self.calls.append(args)
        if self.fail and "create" in args:
            raise RuntimeError("create failed")


def release(tmp_path):
    service = {"image": "example/svc", "container_name": "named"}
    for name in ("app", "edge"):
        (tmp_path / f"{name}.json").write_text(json.dumps({"services": {"svc": service} if name == "app" else {}}))


@pytest.mark.parametrize("change, text", [
    (lambda x: x, "svc: clean"),
    (lambda x: x["HostConfig"].pop("DeviceRequests"), "HostConfig.DeviceRequests"),
    (lambda x: x["HostConfig"]["CapAdd"].pop(), "HostConfig.CapAdd"),
    (lambda x: x["Config"]["Env"].__setitem__(0, "TOKEN=other"), "Config.Env.TOKEN"),
    (lambda x: x["Mounts"].__setitem__(0, {**x["Mounts"][0], "Source": "/srv/homelab/data/x"}), "svc: clean"),
], ids=["identical", "device-request", "capability", "environment", "legacy-bind"])
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


def test_shadow_cleans_up_when_create_fails(tmp_path):
    release(tmp_path)
    docker = Docker(json.loads(FIXTURE.read_text()), fail=True)
    with pytest.raises(RuntimeError):
        shadow(tmp_path, Path("/legacy"), adapter=docker)
    assert docker.calls and docker.calls[-1][-2:] == ["-f", "svc"]
