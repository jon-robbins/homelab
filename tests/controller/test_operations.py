import json
import sys
from pathlib import Path

import scripts.controller.homelabctl as ctl


class RecordingOps:
    def __init__(self):
        self.calls = []
        self.unhealthy = False
        self.probe_failed = False

    def stop(self, service): self.calls.append(("stop", service))
    def restore(self, service, tag): self.calls.append(("restore", service, tag))
    def remove(self, service): self.calls.append(("remove", service))
    def run(self, argv): self.calls.append(("run", argv))
    def healthy(self, service, timeout):
        self.calls.append(("healthy", service, timeout))
        return not self.unhealthy
    def probe(self, service):
        self.calls.append(("probe", service))
        return not self.probe_failed


def _releases(tmp_path, *, state="stateful", edge=None):
    releases = tmp_path / "releases"
    previous = releases / "6"
    current = releases / "7"
    previous.mkdir(parents=True)
    current.mkdir()
    app = {"services": {"svc": {"labels": {"homelab.state": state}}}}
    (previous / "app.json").write_text(json.dumps(app))
    (current / "app.json").write_text(json.dumps(app))
    (current / "edge.json").write_text(json.dumps({"services": edge or {}}))
    (releases / "previous").symlink_to(previous)
    (releases / "current").symlink_to(current)
    return releases


def _run(monkeypatch, tmp_path, argv, ops):
    monkeypatch.setattr(ctl, "SubprocessOps", lambda: ops)
    monkeypatch.setattr(sys, "argv", ["homelabctl", "--state", str(tmp_path / "state"),
                                       "--releases", str(tmp_path / "releases"), *argv])
    return ctl.main()


def test_rollback_stateful_order_and_stateless_up_only(monkeypatch, tmp_path):
    ops = RecordingOps()
    _releases(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "DEGRADED").write_text("service: svc\nsnapshot: /backups/seq-8.tar\n")
    assert _run(monkeypatch, tmp_path, ["rollback", "svc"], ops) == 0
    assert [call[0] for call in ops.calls] == ["stop", "restore", "run"]
    assert ops.calls[1] == ("restore", "svc", "seq-8")

    ops = RecordingOps()
    _releases(tmp_path / "stateless", state="stateless")
    assert _run(monkeypatch, tmp_path / "stateless", ["rollback", "svc"], ops) == 0
    assert [call[0] for call in ops.calls] == ["run"]


def test_rollback_unknown_and_degraded_untouched(monkeypatch, tmp_path):
    releases = _releases(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "DEGRADED").write_text("problem\n")
    ops = RecordingOps()
    assert _run(monkeypatch, tmp_path, ["rollback", "missing"], ops) == 2
    assert (state / "DEGRADED").read_text() == "problem\n"


def test_edge_argv_takeover_and_probe_failure(monkeypatch, tmp_path):
    edge = {"edge": {"labels": {"homelab.probe": "http://:8080/ready"}}, "plain": {}}
    _releases(tmp_path, edge=edge)
    ops = RecordingOps()
    assert _run(monkeypatch, tmp_path, ["apply", "--edge", "--takeover"], ops) == 0
    assert [call[0] for call in ops.calls] == ["remove", "remove", "run", "healthy", "probe", "healthy"]
    assert ops.calls[2][1][2:7] == ["-p", "homelab-edge", "-f", str(tmp_path / "releases" / "current" / "edge.json"), "up"]

    ops = RecordingOps()
    ops.probe_failed = True
    assert _run(monkeypatch, tmp_path, ["apply", "--edge"], ops) == 1
