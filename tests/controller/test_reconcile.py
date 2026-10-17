import json
from pathlib import Path
import sys

import pytest

from scripts.controller.planner import ContainerView
from scripts.controller.state import State
import scripts.controller.reconcile as controller


class Candidate:
    def __init__(self, sequence=7):
        self.sequence = sequence
        self.record_bytes = b"record"
        self.signature_bytes = b"signature"
        self.bundle_bytes = b"bundle"


class Pinger:
    def __init__(self):
        self.calls = []

    def ok(self):
        self.calls.append(("ok", None))

    def fail(self, reason):
        self.calls.append(("fail", reason))


class Ops:
    def __init__(self, services=(), *, free=100):
        self.services = set(services)
        self.free = free
        self.calls = []
        self.snapshot_error = None
        self.pull_error = None
        self.unhealthy = set()

    def reachable(self):
        self.calls.append(("reachable",))
        return True

    def free_pct(self, path):
        self.calls.append(("free", path))
        return self.free

    def config_hash(self, release_dir, project, service):
        return "current" if service in self.services else "old"

    def container(self, project, service):
        if service not in self.services:
            return None
        return ContainerView("current", "running", "")

    def path_exists(self, path):
        return True

    def pull(self, release_dir, services):
        self.calls.append(("pull", services))
        if self.pull_error:
            raise RuntimeError(self.pull_error)

    def stop(self, service):
        self.calls.append(("stop", service))

    def start(self, service):
        self.calls.append(("start", service))

    def run(self, argv):
        self.calls.append(("run", argv))

    def healthy(self, service, timeout_s):
        self.calls.append(("healthy", service))
        return service not in self.unhealthy

    def snapshot(self, service, tag):
        self.calls.append(("snapshot", service, tag))
        if self.snapshot_error:
            raise RuntimeError(self.snapshot_error)
        return f"snapshot-{service}"


def release(tmp_path: Path, sequence=7, *, service="svc", state=None):
    root = tmp_path / "releases" / str(sequence)
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    definition = {"image": "example/svc@sha256:" + "a" * 64}
    if state:
        definition["labels"] = {"homelab.state": state}
    (root / "app.json").write_text(json.dumps({"services": {service: definition}}))
    (root / "edge.json").write_text(json.dumps({"services": {}}))
    (root / "site.yaml").write_text(f"managed: [{service}]\nservices: {{}}\n")
    return root


def prepared(monkeypatch, tmp_path, ops, candidate=None, *, release_state=None, verify_error=None):
    candidate = candidate or Candidate()
    root = release(tmp_path, candidate.sequence, state=release_state)

    def fetch(highwater, policy):  # fetch_newest verifies the record; a verify error surfaces from it
        if verify_error:
            raise RuntimeError(verify_error)
        return candidate

    monkeypatch.setattr(controller, "fetch_newest", fetch)
    monkeypatch.setattr(controller, "materialize", lambda value, releases: root)
    return State(tmp_path / "state"), root, candidate


@pytest.mark.parametrize(
    "state_file, expected", [("applying", "interrupted apply of seq 4"), ("DEGRADED", "already degraded")]
)
def test_existing_failure_stops_without_ops(tmp_path, state_file, expected):
    state = State(tmp_path / "state")
    state.write(state_file, "4\n" if state_file == "applying" else expected + "\n")
    ops = Ops()
    pinger = Pinger()

    result = controller.reconcile(state, object(), tmp_path / "releases", ops, pinger, now=False)

    assert expected in result
    assert not [call for call in ops.calls if call[0] != "reachable"]
    assert pinger.calls[-1][0] == "fail"
    if state_file == "applying":
        assert state.highwater() == 4


def test_unreachable_and_no_candidate(tmp_path, monkeypatch):
    state = State(tmp_path / "state")
    ops = Ops()
    ops.reachable = lambda: False
    pinger = Pinger()
    assert controller.reconcile(state, object(), tmp_path / "releases", ops, pinger, now=False) == "docker unreachable"
    assert pinger.calls == [("fail", "docker unreachable")]

    ops = Ops()
    monkeypatch.setattr(controller, "fetch_newest", lambda highwater, policy: None)
    pinger = Pinger()
    assert controller.reconcile(state, object(), tmp_path / "releases", ops, pinger, now=False) == "ok"
    assert pinger.calls == [("ok", None)]


def test_observe_only_does_not_mutate(tmp_path, monkeypatch):
    ops = Ops()
    state, _, candidate = prepared(monkeypatch, tmp_path / "observe", ops)
    state.write("observe-only", "1\n")
    pinger = Pinger()
    assert controller.reconcile(state, object(), tmp_path / "releases", ops, pinger, now=False) == "ok"
    assert not {call[0] for call in ops.calls} & {"pull", "stop", "start", "run", "snapshot"}


def test_empty_plan_moves_pointers(tmp_path, monkeypatch):
    ops = Ops(services={"svc"})
    state, _, candidate = prepared(monkeypatch, tmp_path, ops)
    pinger = Pinger()

    assert controller.reconcile(state, object(), tmp_path / "releases", ops, pinger, now=False) == "ok"
    assert state.highwater() == candidate.sequence
    assert (tmp_path / "releases" / "current").resolve() == (tmp_path / "releases" / "7").resolve()
    assert pinger.calls == [("ok", None)]


def test_preapply_failures_keep_highwater_and_fail_on_third(tmp_path, monkeypatch):
    ops = Ops()
    ops.pull_error = "pull failed"
    state, _, candidate = prepared(monkeypatch, tmp_path, ops)
    pinger = Pinger()
    for count in range(1, 4):
        result = controller.reconcile(state, object(), tmp_path / "releases", ops, pinger, now=False)
        assert f"pre-apply failed {count}×" in result
        assert state.highwater() == 0
    assert pinger.calls[-1][0] == "fail"


def test_materialization_failure_retries_and_disk_cause_fails_ping(tmp_path, monkeypatch):
    ops = Ops(free=5)
    state, _, _ = prepared(monkeypatch, tmp_path, ops)

    def fail_materialize(*args):
        raise RuntimeError("bad bundle")

    monkeypatch.setattr(controller, "materialize", fail_materialize)
    pinger = Pinger()

    assert "pre-apply failed 1×: bad bundle" == controller.reconcile(
        state, object(), tmp_path / "releases", ops, pinger, now=False
    )
    assert state.highwater() == 0
    assert state.read("DEGRADED") is None
    assert pinger.calls[-1] == ("fail", "low free space on /; low free space on /mnt/media-hdd")


def test_snapshot_error_starts_service_again(tmp_path, monkeypatch):
    ops = Ops(services=set())
    ops.snapshot_error = "backup failed"
    state, _, _ = prepared(monkeypatch, tmp_path, ops, release_state="stateful")

    controller.reconcile(state, object(), tmp_path / "releases", ops, Pinger(), now=False)

    assert ("stop", "svc") in ops.calls
    assert ("start", "svc") in ops.calls


def test_pass_records_pointers_and_highwater(tmp_path, monkeypatch):
    ops = Ops()
    state, _, candidate = prepared(monkeypatch, tmp_path, ops)
    pinger = Pinger()

    assert controller.reconcile(state, object(), tmp_path / "releases", ops, pinger, now=False) == "ok"
    assert state.highwater() == candidate.sequence
    assert state.read("applying") is None
    assert ("run",) == tuple(call[:1] for call in ops.calls if call[0] == "run")[0]


def test_second_service_failure_records_its_snapshot(tmp_path, monkeypatch):
    ops = Ops()
    state, root, candidate = prepared(monkeypatch, tmp_path, ops, release_state="stateful")
    app = json.loads((root / "app.json").read_text())
    app["services"]["zeta"] = dict(app["services"]["svc"])
    (root / "app.json").write_text(json.dumps(app))
    (root / "site.yaml").write_text("managed: [svc, zeta]\nservices: {}\n")
    ops.unhealthy.add("zeta")

    controller.reconcile(state, object(), tmp_path / "releases", ops, Pinger(), now=False)

    assert state.highwater() == candidate.sequence
    assert "service: zeta\nsnapshot: snapshot-zeta" in state.read("DEGRADED")
    assert "homelabctl rollback zeta" in state.read("DEGRADED")


def test_fetch_or_verify_failure_retries_without_advancing_highwater(tmp_path, monkeypatch):
    ops = Ops()
    state, _, candidate = prepared(monkeypatch, tmp_path, ops, verify_error="bad record")
    pinger = Pinger()

    result = controller.reconcile(state, object(), tmp_path / "releases", ops, pinger, now=False)

    assert result == "pre-apply failed 1×: bad record"
    assert state.highwater() == 0
    assert state.read("failures").strip() == "1"
    assert state.read("DEGRADED") is None
    assert not ops.calls or all(call[0] not in {"pull", "stop", "run"} for call in ops.calls)


def test_causes_make_final_ping_fail_and_ack_removes_degraded(tmp_path, monkeypatch):
    state = State(tmp_path / "state")
    ops = Ops(free=5)
    monkeypatch.setattr(controller, "fetch_newest", lambda highwater, policy: None)
    pinger = Pinger()
    controller.reconcile(state, object(), tmp_path / "releases", ops, pinger, now=False)
    assert pinger.calls[-1] == ("fail", "low free space on /; low free space on /mnt/media-hdd")

    state.write("DEGRADED", "problem\n")
    monkeypatch.setattr(sys, "argv", ["homelabctl", "--state", str(tmp_path / "state"), "ack", "--reason", "fixed"])
    import scripts.controller.homelabctl as homelabctl

    assert homelabctl.main() == 0
    assert state.read("DEGRADED") is None
    assert "fixed" in (tmp_path / "state" / "ack.log").read_text()


def test_subprocess_ops_reads_docker_state(monkeypatch, tmp_path):
    seen = []

    def output(argv, **kwargs):
        seen.append(argv)
        if argv[1] == "compose":
            return "svc hash\n"
        if argv[1] == "ps":
            return "container-id\n"
        return json.dumps(
            [
                {
                    "Config": {"Labels": {"com.docker.compose.config-hash": "hash"}},
                    "State": {"Status": "running", "Error": "", "Health": {"Status": "healthy"}},
                    "RepoDigests": ["example/svc@sha256:" + "b" * 64],
                    "Image": "sha256:image",
                }
            ]
        )

    monkeypatch.setattr(controller.subprocess, "check_output", output)
    ops = controller.SubprocessOps()
    assert ops.config_hash(tmp_path, "homelab", "svc") == "hash"
    assert ops.container("homelab", "svc") == ContainerView("hash", "running", "")
    ops.config_hash(tmp_path, "homelab-edge", "proxy")
    assert str(tmp_path.resolve() / "edge.json") in seen[-1]


def test_subprocess_ops_uses_compose_container_for_stop_and_start(monkeypatch):
    calls = []
    monkeypatch.setattr(controller.subprocess, "check_output", lambda *args, **kwargs: "generated-id\n")
    monkeypatch.setattr(controller.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    ops = controller.SubprocessOps()

    ops.stop("svc")
    ops.start("svc")

    assert calls == [["docker", "stop", "generated-id"], ["docker", "start", "generated-id"]]


def test_subprocess_ops_ignores_absent_container_for_stop_and_start(monkeypatch):
    calls = []
    monkeypatch.setattr(controller.subprocess, "check_output", lambda *args, **kwargs: "")
    monkeypatch.setattr(controller.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    ops = controller.SubprocessOps()

    ops.stop("svc")
    ops.start("svc")

    assert calls == []


def test_status_prints_pointer_sequences(monkeypatch, tmp_path, capsys):
    releases = tmp_path / "releases"
    for sequence in (6, 7):
        release(releases.parent, sequence)
    (releases / "current").symlink_to(releases / "7")
    (releases / "previous").symlink_to(releases / "6")
    monkeypatch.setattr(
        sys, "argv", ["homelabctl", "--state", str(tmp_path / "state"), "--releases", str(releases), "status"]
    )
    import scripts.controller.homelabctl as homelabctl

    assert homelabctl.main() == 0
    assert "current=7 previous=6 highwater=0" in capsys.readouterr().out


def test_subprocess_ops_waits_for_docker_health(monkeypatch):
    states = iter(
        [
            {"Status": "running", "Health": {"Status": "starting"}},
            {"Status": "running", "Health": {"Status": "healthy"}},
        ]
    )
    ops = controller.SubprocessOps()
    monkeypatch.setattr(ops, "_inspect", lambda service, project="homelab": {"State": next(states)})
    monkeypatch.setattr(controller.time, "sleep", lambda seconds: None)

    assert ops.healthy("svc", 180)
    monkeypatch.setattr(
        ops,
        "_inspect",
        lambda service, project="homelab": {"State": {"Status": "running", "Health": {"Status": "unhealthy"}}},
    )
    ticks = iter([0, 1])
    monkeypatch.setattr(controller.time, "monotonic", lambda: next(ticks))
    assert not ops.healthy("svc", 1)
