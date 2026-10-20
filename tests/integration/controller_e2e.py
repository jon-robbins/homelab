"""Hosted-Docker end-to-end coverage for the host controller."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from scripts.controller.admission import HostPolicy
from scripts.controller.feed import FeedPolicy
from scripts.controller.planner import plan
from scripts.controller.reconcile import SubprocessOps, reconcile
from scripts.controller.state import State
from scripts.release.bundle import build, bundle_sha256


ROOT = Path(__file__).parents[2]


def run(*argv: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(argv, check=True, text=True, capture_output=True, env=env).stdout


def inspect(service: str, *format_args: str) -> str:
    return run("docker", "inspect", "-f", *format_args, f"homelab-{service}-1").strip()


def app_definition(*, version: str = "one", broken_nginx: bool = False,
                   state_command: str | None = None) -> dict:
    state_command = state_command or "cp /data/one.sqlite /data/state.sqlite; sleep 3600"
    health = ["CMD-SHELL", "false"] if broken_nginx else ["CMD", "nginx", "-t"]
    return {"services": {
        "nginx": {"image": "nginx:alpine", "healthcheck": {"test": health, "interval": "1s", "retries": 1}},
        "whoami": {"image": "traefik/whoami:v1.10.3", "environment": [f"E2E_VERSION={version}"]},
        "stateful": {"image": "busybox:1.36", "command": ["sh", "-c", state_command],
                     "labels": {"homelab.state": "stateful"},
                     "depends_on": ["nginx"],
                     "volumes": [{"type": "bind", "source": "DATA_ROOT", "target": "/data"}]},
        "stopped": {"image": "busybox:1.36", "command": ["sleep", "3600"],
                    "depends_on": ["nginx"]},
    }}


def site_definition() -> str:
    return ("managed: [nginx, whoami, stateful, stopped]\nservices:\n"
            "  nginx: {}\n  whoami: {}\n  stateful: {}\n  stopped: {state: stopped}\n")


class Harness:
    def __init__(self, root: Path):
        self.root = root
        self.feed = root / "feed"
        self.trust = root / "trust"
        self.releases = root / "releases"
        self.state_dir = root / "state"
        self.data = root / "data"
        self.feed.mkdir()
        self.trust.mkdir()
        self.data.mkdir()
        for value in ("one", "two", "three"):
            with sqlite3.connect(self.data / f"{value}.sqlite") as db:
                db.execute("CREATE TABLE state (value TEXT)")
                db.execute("INSERT INTO state VALUES (?)", (value,))
        (self.data / "state.sqlite").write_bytes((self.data / "one.sqlite").read_bytes())
        (self.trust / "trusted-root.json").write_text("{}")
        (self.trust / "feed.token").write_text("")
        self.policy_obj = HostPolicy("e2e", "e2e", "e2e", self.trust / "trusted-root.json", self.feed)
        self.policy_file = root / "policy.json"
        self.policy_file.write_text(json.dumps({
            "schema": 1, "site_id": "e2e", "signer_identity": "e2e", "oidc_issuer": "e2e",
            "trusted_root": str(self.policy_obj.trusted_root), "feed_directory": str(self.feed)}))
        self.verifier_log = root / "verifier.log"
        verifier = root / "bin" / "cosign"
        verifier.parent.mkdir()
        verifier.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_VERIFIER_LOG\"\n")
        verifier.chmod(0o755)
        backup = verifier.parent / "homelab-backup"
        backup.write_text("""#!/bin/sh
set -eu
[ "$3" = --tag ] || exit 2  # same argument check as host/homelab-backup
archive="$E2E_BACKUP_ROOT/$4.tar"
if [ "$1" = --restore ]; then
  printf '%s\n' "$4" >> "$E2E_RESTORE_LOG"
  rm -rf "$E2E_DATA_ROOT"/*
  tar -xf "$archive" -C "$E2E_DATA_ROOT"
else
  mkdir -p "$E2E_BACKUP_ROOT"
  tar -C "$E2E_DATA_ROOT" -cf "$archive" .
  printf '%s\\n' "$archive"
fi
""")
        backup.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = f"{verifier.parent}:{self.env['PATH']}"
        self.env["FAKE_VERIFIER_LOG"] = str(self.verifier_log)
        self.env["E2E_DATA_ROOT"] = str(self.data)
        self.env["HOMELAB_DATA_ROOT"] = str(self.data.parent)
        self.env["E2E_BACKUP_ROOT"] = str(root / "backups")
        self.env["E2E_RESTORE_LOG"] = str(root / "restore.log")
        self.feed_git = ["git", "-C", str(self.feed)]
        run("git", "init", "--quiet", str(self.feed), env=self.env)
        run(*self.feed_git, "config", "user.email", "e2e@example.invalid", env=self.env)
        run(*self.feed_git, "config", "user.name", "e2e", env=self.env)

    def release(self, sequence: int, *, version: str = "one", broken_nginx: bool = False,
                state_command: str | None = None) -> None:
        source = self.root / f"source-{sequence}"
        source.mkdir(parents=True)
        app = app_definition(version=version, broken_nginx=broken_nginx, state_command=state_command)
        app_text = json.dumps(app).replace("DATA_ROOT", str(self.data))
        (source / "app.json").write_text(app_text)
        (source / "edge.json").write_text(json.dumps({"services": {}}))
        (source / "site.yaml").write_text(site_definition())
        bundle = build(source)
        record = {"schema": 1, "site_id": "e2e", "sequence": sequence,
                  "tooling_git_sha": "0" * 40,
                  "sources": {"homelab": "0" * 40, "homelab-site": "0" * 40},
                  "images": {}, "bundle_sha256": bundle_sha256(bundle), "rollback_floor": 0}
        feed_record = self.feed / "records" / str(sequence)
        feed_record.mkdir(parents=True, exist_ok=True)
        (feed_record / "record.json").write_text(json.dumps(record, separators=(",", ":")))
        (feed_record / "record.sigstore.json").write_text("fake")
        (feed_record / "bundle.tar").write_bytes(bundle)
        run(*self.feed_git, "add", ".", env=self.env)
        run(*self.feed_git, "commit", "--quiet", "-m", str(sequence), env=self.env)
        run(*self.feed_git, "branch", "-f", "releases", "HEAD", env=self.env)

    def policy(self) -> FeedPolicy:
        return FeedPolicy(str(self.feed), self.trust, self.policy_obj)

    def reconcile(self, *, now: bool = False) -> str:
        os.environ.update(self.env)
        ops = SubprocessOps()
        free_pct = ops.free_pct
        ops.free_pct = lambda path: free_pct(str(self.data) if path == "/mnt/media-hdd" else path)
        return reconcile(State(self.state_dir), self.policy(), self.releases,
                         ops, Pinger(), now=now)

    def cleanup(self) -> None:
        subprocess.run(["docker", "compose", "-p", "homelab", "-f",
                        str(self.releases / "current" / "app.json"), "down", "-v", "--remove-orphans"],
                       check=False, capture_output=True, env=self.env)


class Pinger:
    def ok(self) -> None: pass
    def fail(self, reason: str) -> None: pass


@pytest.fixture
def harness(tmp_path: Path):
    value = Harness(tmp_path)
    try:
        yield value
    finally:
        value.cleanup()


def install_first(h: Harness) -> str:
    h.release(1)
    run("docker", "compose", "-p", "homelab", "-f", str(h.root / "source-1" / "app.json"),
        "up", "-d", "nginx", "stateful", env=h.env)
    nginx_id = inspect("nginx", "{{.Id}}")
    assert h.reconcile() == "ok"
    return nginx_id


def state_value(h: Harness) -> str:
    with sqlite3.connect(h.data / "state.sqlite") as db:
        return db.execute("SELECT value FROM state").fetchone()[0]


def test_adopt_stopped_service_without_recreating_dependency(harness: Harness):
    h = harness
    nginx_id = install_first(h)
    assert inspect("stopped", "{{.State.Status}}") == "created"
    assert inspect("nginx", "{{.State.Status}}") == "running"
    assert inspect("nginx", "{{.Id}}") == nginx_id
    assert h.reconcile() == "ok"
    assert inspect("stopped", "{{.State.Status}}") == "created"


def test_change_applies_and_next_plan_is_empty(harness: Harness):
    h = harness
    install_first(h)
    h.release(2, state_command="cp /data/one.sqlite /data/state.sqlite; sleep 3601")
    assert h.reconcile() == "ok"
    assert h.state_dir.joinpath("highwater").read_text().strip() == "2"
    next_plan = plan(h.releases / "current", SubprocessOps())
    assert next_plan.recreate == ()
    assert next_plan.create_stopped == ()
    assert h.reconcile() == "ok"


def test_broken_stateless_healthcheck_rolls_back_and_acknowledges(harness: Harness):
    h = harness
    install_first(h)
    h.release(2, version="two")
    assert h.reconcile() == "ok"
    h.release(3, version="three", broken_nginx=True)
    assert "service: nginx" in h.reconcile()
    assert h.state_dir.joinpath("DEGRADED").exists()
    run(sys.executable, str(ROOT / "scripts/controller/homelabctl.py"), "--state", str(h.state_dir),
        "--releases", str(h.releases), "rollback", "nginx", env=h.env)
    assert json.loads(inspect("nginx", "{{json .Config.Healthcheck.Test}}")) == ["CMD", "nginx", "-t"]
    run(sys.executable, str(ROOT / "scripts/controller/homelabctl.py"), "--state", str(h.state_dir),
        "ack", "--reason", "e2e", env=h.env)
    assert not h.state_dir.joinpath("DEGRADED").exists()


def test_stateful_failure_halts_with_snapshot_and_restores_data(harness: Harness):
    h = harness
    install_first(h)
    h.release(2, state_command="if [ ! -f /data/two-written ]; then cp /data/two.sqlite /data/state.sqlite; touch /data/two-written; fi; sleep 3600")
    assert h.reconcile() == "ok"
    for _ in range(50):
        if (h.data / "two-written").exists():
            break
        time.sleep(0.1)
    assert state_value(h) == "two"
    h.release(3, state_command="cp /data/three.sqlite /data/state.sqlite; exit 1")
    assert "snapshot: " in h.reconcile()
    assert h.state_dir.joinpath("DEGRADED").exists()
    assert "seq-3.tar" in h.state_dir.joinpath("DEGRADED").read_text()
    run(sys.executable, str(ROOT / "scripts/controller/homelabctl.py"), "--state", str(h.state_dir),
        "--releases", str(h.releases), "rollback", "stateful", env=h.env)
    assert h.root.joinpath("restore.log").read_text().strip() == "seq-3"
    assert state_value(h) == "two"


def test_break_glass_compose_command_starts_service(harness: Harness):
    h = harness
    install_first(h)
    run("docker", "stop", "homelab-whoami-1", env=h.env)
    assert inspect("whoami", "{{.State.Status}}") == "exited"
    run("docker", "compose", "-p", "homelab", "-f", str(h.releases / "current" / "app.json"),
        "up", "-d", "--no-deps", "whoami", env=h.env)
    assert inspect("whoami", "{{.State.Status}}") == "running"


def test_owner_stopped_container_stays_stopped_after_now_reconcile(harness: Harness):
    h = harness
    install_first(h)
    run("docker", "stop", "homelab-whoami-1", env=h.env)
    h.release(2, state_command="cp /data/one.sqlite /data/state.sqlite; sleep 3601")
    assert h.reconcile(now=True) == "ok"
    assert inspect("whoami", "{{.State.Status}}") == "exited"
