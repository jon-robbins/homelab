"""Contract G validation, command serialization and real process-crash recovery."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import json
import os
import selectors
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from scripts.controller.state import (
    CorruptStateError, MissingStateError, MutationLock,
    SchemaVersionError, State, StateError, validate_frozen_plan,
)


DIGEST = "sha256:" + "a" * 64
IMAGE = "registry.example/app@" + DIGEST
AT = "2026-10-13T00:00:00Z"
PLAN = {
    "changed_services": ["app"],
    "service_steps": {"app": {"operation": "replace", "may_have_run": False}},
    "manual_recovery_required": False,
    "stopped_for_maintenance": [],
    "snapshots": {},
    "maintenance_permission": {
        "record_sha256": DIGEST, "participants": ["app"],
        "expires_at": AT, "consumed": False,
    },
    "qualifier": {
        "profile": "gpu", "container_names": ["qualifier"],
        "hardware_and_inputs": {
            "image_manifest": IMAGE, "local_image_id": DIGEST,
            "bundle_sha256": DIGEST, "model_manifest_sha256": None,
            "fixture_sha256": DIGEST, "gpu_uuid": "gpu-example",
            "kernel_release": "example", "boot_id": "example",
            "nvidia_driver_version": "example",
            "nvidia_container_runtime_version": "example",
            "cuda_runtime_version": "example", "trusted_profile_sha256": DIGEST,
        },
        "result": "pending", "baseline_restored": False,
    },
}
IMAGES = {"app": {"ref": IMAGE, "local_image_id": None}}
STAMPS = {"APP_KEY": {"device": 0, "inode": 1, "size": 2, "mtime_ns": 3}}


def initialized(tmp_path, **kwargs):
    return State.init(tmp_path, site_id="site-a", highwater=4, rollback_floor=2, **kwargs)


@pytest.fixture
def state(tmp_path):
    result = initialized(tmp_path)
    try:
        yield result
    finally:
        result.close()


def seed(state):
    state.add_release(5, DIGEST, b"record", b"signature", DIGEST, "pending", IMAGES)
    state.add_attempt(kind="deploy", release_seq=5, baseline_seq=None, plan=PLAN,
                      recovery_action="none", started_at=AT)
    state.add_observation(service="app", observed_at=AT, container_id=None,
                          image_id=None, spec_matches=True, health="pass",
                          semantic="pass", loaded_secrets=STAMPS)
    state.add_event(AT, "controller", "prepare", "candidate admitted", 1)


def metadata(state):
    return {table: state.conn.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("control", "releases", "attempts", "observations", "events")}


def test_schema_pragmas_and_init(state, tmp_path):
    assert state.control()["site_id"] == "site-a"
    for handle in (state, State.open(tmp_path)):
        assert handle.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert handle.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert handle.conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        if handle is not state:
            handle.close()
    columns = {
        "control": "id schema site_id phase highwater rollback_floor current_seq previous_seq active_attempt_id degraded_reason",
        "releases": "sequence record_sha256 record_bytes signature_bytes bundle_sha256 outcome images",
        "attempts": "id kind release_seq baseline_seq plan recovery_action started_at finished_at failure",
        "observations": "service observed_at container_id image_id spec_matches health semantic loaded_secrets",
        "events": "id at actor action attempt_id detail",
    }
    assert {r[0] for r in state.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == set(columns)
    for table, names in columns.items():
        info = state.conn.execute(f"PRAGMA table_info({table})").fetchall()
        assert [r[1] for r in info] == names.split()
        assert [r[1] for r in info if r[5]] == [names.split()[0]]
    foreign_keys = {
        "control": {("current_seq", "releases", "sequence"), ("previous_seq", "releases", "sequence"), ("active_attempt_id", "attempts", "id")},
        "attempts": {("release_seq", "releases", "sequence"), ("baseline_seq", "releases", "sequence")},
        "events": {("attempt_id", "attempts", "id")},
        "releases": set(), "observations": set(),
    }
    for table, expected in foreign_keys.items():
        assert {(r[3], r[2], r[4]) for r in state.conn.execute(f"PRAGMA foreign_key_list({table})")} == expected
    before = metadata(state)
    with pytest.raises(StateError, match="already exists"):
        initialized(tmp_path)
    assert metadata(state) == before
    with pytest.raises(sqlite3.IntegrityError):
        state.conn.execute("INSERT INTO control SELECT 2,schema,site_id,phase,highwater,rollback_floor,current_seq,previous_seq,active_attempt_id,degraded_reason FROM control")
    with pytest.raises(sqlite3.IntegrityError):
        state.conn.execute("UPDATE control SET schema=2")


@pytest.mark.parametrize("table,column,values", [
    ("control", "phase", "IDLE PREPARE APPLY VERIFY FAIL DEGRADED"),
    ("releases", "outcome", "pending committed failed skipped"),
    ("attempts", "kind", "deploy secret-activation recovery"),
    ("attempts", "recovery_action", "none restore-baseline manual"),
    ("observations", "health", "pass fail error"),
    ("observations", "semantic", "pass fail error"),
    ("events", "actor", "controller operator"),
    ("observations", "spec_matches", (0, 1)),
])
def test_sql_enum_constraints(state, table, column, values):
    seed(state)
    for value in values.split() if isinstance(values, str) else values:
        with state.transaction() as conn:
            conn.execute(f"UPDATE {table} SET {column}=?", (value,))
    before = metadata(state)
    for value in ("invalid", "Pass", "idle", None, 42):
        with pytest.raises(sqlite3.IntegrityError):
            with state.transaction() as conn:
                conn.execute(f"UPDATE {table} SET {column}=?", (value,))
        assert metadata(state) == before


@pytest.mark.parametrize("table,column", [
    ("control", "current_seq"), ("control", "previous_seq"),
    ("control", "active_attempt_id"), ("attempts", "release_seq"),
    ("attempts", "baseline_seq"), ("events", "attempt_id"),
])
def test_foreign_keys_enforced(state, table, column):
    seed(state)
    before = metadata(state)
    with pytest.raises(sqlite3.IntegrityError):
        with state.transaction() as conn:
            conn.execute(f"UPDATE {table} SET {column}=999")
    assert metadata(state) == before


@pytest.mark.parametrize("table,column", [
    ("control", "degraded_reason"), ("attempts", "failure"), ("events", "detail"),
])
def test_sql_bounded_strings(state, table, column):
    seed(state)
    with state.transaction() as conn:
        conn.execute(f"UPDATE {table} SET {column}=?", ("x" * 1024,))
    before = metadata(state)
    with pytest.raises(sqlite3.IntegrityError):
        with state.transaction() as conn:
            conn.execute(f"UPDATE {table} SET {column}=?", ("x" * 1025,))
    assert metadata(state) == before


def test_release_unique_hash_and_positive_sequence(state):
    seed(state)
    before = metadata(state)
    with pytest.raises(sqlite3.IntegrityError):
        state.add_release(6, DIGEST, b"record", b"signature", DIGEST, "pending", {})
    for value in (0, -1):
        with pytest.raises(sqlite3.IntegrityError):
            state.conn.execute("UPDATE releases SET sequence=?", (value,))
    assert metadata(state) == before


def test_missing_corrupt_and_wrong_schema_fail_closed(tmp_path):
    with pytest.raises(MissingStateError):
        State.open(tmp_path)
    assert not (tmp_path / "controller.sqlite3").exists()
    db = tmp_path / "controller.sqlite3"
    db.write_bytes(b"not a sqlite database")
    with pytest.raises(CorruptStateError):
        State.open(tmp_path)
    assert db.read_bytes() == b"not a sqlite database"
    db.unlink()
    state = initialized(tmp_path)
    # Simulate an intact future schema, rather than breaking v1 CHECK(schema=1).
    state.conn.execute("ALTER TABLE control RENAME COLUMN schema TO old_schema")
    state.conn.execute("ALTER TABLE control ADD COLUMN schema INTEGER NOT NULL DEFAULT 2")
    state.close()
    with pytest.raises(SchemaVersionError):
        State.open(tmp_path)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT schema,highwater,rollback_floor FROM control").fetchone() == (2, 4, 2)


def test_integrity_check_failure_and_missing_row(state, tmp_path):
    state.conn.execute("PRAGMA ignore_check_constraints=ON")
    state.conn.execute("UPDATE control SET highwater=-1")
    state.conn.execute("PRAGMA ignore_check_constraints=OFF")
    assert state.conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
    with pytest.raises(CorruptStateError, match="integrity"):
        State.open(tmp_path)
    state.conn.execute("DELETE FROM control")
    with pytest.raises(CorruptStateError, match="missing"):
        State.open(tmp_path)
    assert state.conn.execute("SELECT count(*) FROM control").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["highwater", "rollback_floor"])
def test_monotonic_counters(state, field):
    before = metadata(state)
    with pytest.raises(ValueError, match="cannot decrease"):
        state.set_counters(**{field: state.control()[field] - 1})
    assert metadata(state) == before
    with pytest.raises(ValueError, match="cannot decrease"):
        with state.transaction() as conn:
            conn.execute(f"UPDATE control SET {field}={field}-1,phase='PREPARE'")
    assert metadata(state) == before
    with pytest.raises(ValueError):
        state.set_counters(**{field: True})
    assert metadata(state) == before
    state.set_counters(**{field: 10})
    assert state.control()[field] == 10


# Invalid nested records exercise both the pending transaction and reopen paths.
INVALID_RECORDS = [
    ("images", (), []),
    ("images", ("BadName",), {"ref": IMAGE, "local_image_id": None}),
    ("images", ("app",), {}),
    ("images", ("app", "extra"), True),
    ("images", ("app", "ref"), "registry.example/app:latest"),
    ("images", ("app", "local_image_id"), 123),
    ("images", ("app", "local_image_id"), "sha256:bad"),
    ("plan", (), {}),
    ("plan", ("extra",), True),
    ("plan", ("changed_services",), "app"),
    ("plan", ("changed_services",), ["BadName"]),
    ("plan", ("stopped_for_maintenance",), [False]),
    ("plan", ("manual_recovery_required",), 1),
    ("plan", ("service_steps",), []),
    ("plan", ("service_steps", "app", "operation"), "REPLACE"),
    ("plan", ("service_steps", "app", "may_have_run"), 1),
    ("plan", ("service_steps", "app", "extra"), True),
    ("plan", ("snapshots",), []),
    ("plan", ("snapshots", "app"), {"path": "/etc/shadow", "verified": True}),
    ("plan", ("snapshots", "app"), {"path": "relative", "verified": True}),
    ("plan", ("maintenance_permission",), {}),
    ("plan", ("maintenance_permission", "extra"), True),
    ("plan", ("maintenance_permission", "record_sha256"), "invalid"),
    ("plan", ("maintenance_permission", "participants"), "app"),
    ("plan", ("maintenance_permission", "participants"), ["BadName"]),
    ("plan", ("maintenance_permission", "expires_at"), "yesterday"),
    ("plan", ("maintenance_permission", "consumed"), 0),
    ("plan", ("qualifier",), {}),
    ("plan", ("qualifier", "extra"), True),
    ("plan", ("qualifier", "profile"), "BadName"),
    ("plan", ("qualifier", "container_names"), [False]),
    ("plan", ("qualifier", "result"), "PASS"),
    ("plan", ("qualifier", "baseline_restored"), 1),
    ("plan", ("qualifier", "hardware_and_inputs"), {}),
    ("plan", ("qualifier", "hardware_and_inputs", "extra"), True),
    ("plan", ("qualifier", "hardware_and_inputs", "image_manifest"), "latest"),
    ("loaded_secrets", (), []),
    ("loaded_secrets", ("bad-name",), STAMPS["APP_KEY"]),
    ("loaded_secrets", ("APP_KEY",), {}),
    ("loaded_secrets", ("APP_KEY", "extra"), 0),
]
for field in ("local_image_id", "bundle_sha256", "model_manifest_sha256", "fixture_sha256", "trusted_profile_sha256"):
    INVALID_RECORDS.append(("plan", ("qualifier", "hardware_and_inputs", field), "bad"))
for field in ("gpu_uuid", "kernel_release", "boot_id", "nvidia_driver_version", "nvidia_container_runtime_version", "cuda_runtime_version"):
    for value in (False, "x" * 257):
        INVALID_RECORDS.append(("plan", ("qualifier", "hardware_and_inputs", field), value))
for field in ("device", "inode", "size", "mtime_ns"):
    for value in (-1, True, 1.5, "1"):
        INVALID_RECORDS.append(("loaded_secrets", ("APP_KEY", field), value))


def invalid_record(column, path, value):
    record = deepcopy({"plan": PLAN, "images": IMAGES, "loaded_secrets": STAMPS}[column])
    if not path:
        return value
    target = record
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return record


TABLE = {"plan": "attempts", "images": "releases", "loaded_secrets": "observations"}


@pytest.mark.parametrize("column,path,value", INVALID_RECORDS)
def test_invalid_json_rolls_back_entire_transition(state, column, path, value):
    seed(state)
    before = metadata(state)
    with pytest.raises(ValueError, match="invalid state transition"):
        with state.transaction() as conn:
            conn.execute("UPDATE control SET phase='PREPARE',highwater=5")
            conn.execute("UPDATE releases SET outcome='failed'")
            conn.execute(f"UPDATE {TABLE[column]} SET {column}=?", (json.dumps(invalid_record(column, path, value)),))
    assert metadata(state) == before


@pytest.mark.parametrize("column,path,value", INVALID_RECORDS)
def test_invalid_json_fails_closed_on_read(state, tmp_path, column, path, value):
    seed(state)
    state.conn.execute(f"UPDATE {TABLE[column]} SET {column}=?", (json.dumps(invalid_record(column, path, value)),))
    before = metadata(state)
    with pytest.raises(CorruptStateError):
        State.open(tmp_path)
    assert metadata(state) == before


@pytest.mark.parametrize("column", TABLE)
def test_malformed_json_rejected_on_write_and_read(state, tmp_path, column):
    seed(state)
    before = metadata(state)
    with pytest.raises(ValueError):
        with state.transaction() as conn:
            conn.execute(f"UPDATE {TABLE[column]} SET {column}='{{'")
    assert metadata(state) == before
    state.conn.execute(f"UPDATE {TABLE[column]} SET {column}='{{'")
    with pytest.raises(CorruptStateError):
        State.open(tmp_path)


def test_valid_nested_records_and_helpers(state, tmp_path):
    seed(state)
    with state.transaction() as conn:
        images = deepcopy(IMAGES)
        images["app"]["local_image_id"] = DIGEST
        conn.execute("UPDATE releases SET images=?", (json.dumps(images),))
        plan = deepcopy(PLAN)
        plan["qualifier"]["hardware_and_inputs"]["model_manifest_sha256"] = DIGEST
        plan["qualifier"]["hardware_and_inputs"]["gpu_uuid"] = "x" * 256
        conn.execute("UPDATE attempts SET plan=?", (json.dumps(plan),))
    reopened = State.open(tmp_path)
    assert metadata(reopened) == metadata(state)
    reopened.close()
    for operation in ("create", "replace", "remove"):
        plan["service_steps"]["app"]["operation"] = operation
        for result in ("pending", "pass", "fail", "interrupted"):
            plan["qualifier"]["result"] = result
            assert validate_frozen_plan(plan) is plan
    minimal = {k: v for k, v in PLAN.items() if k not in ("maintenance_permission", "qualifier")}
    assert validate_frozen_plan(minimal) is minimal
    before = metadata(state)
    with pytest.raises(ValueError):
        state.add_release(6, DIGEST, b"record", b"signature", DIGEST, "pending", {"app": {}})
    with pytest.raises(ValueError):
        state.add_attempt(kind="deploy", release_seq=5, baseline_seq=None, plan={}, recovery_action="none", started_at=AT)
    with pytest.raises(ValueError):
        state.add_observation(service="another", observed_at=AT, container_id=None, image_id=None,
                              spec_matches=True, health="pass", semantic="pass", loaded_secrets={"APP_KEY": {}})
    assert metadata(state) == before


@pytest.mark.parametrize("escape", ["absolute", "traversal", "symlink", "sibling", "unconfigured"])
def test_snapshot_roots_on_write_and_read(tmp_path, escape):
    root = tmp_path / "backups"
    root.mkdir()
    state = initialized(tmp_path, backup_roots=(root,))
    try:
        seed(state)
        plan = deepcopy(PLAN)
        plan["snapshots"] = {"app": {"path": str(root / "app" / "snapshot"), "verified": True}}
        with state.transaction() as conn:
            conn.execute("UPDATE attempts SET plan=?", (json.dumps(plan),))
        reopened = State.open(tmp_path, backup_roots=(root,))
        reopened.close()
        (root / "escape").symlink_to(tmp_path, target_is_directory=True)
        paths = {
            "absolute": "/etc/shadow", "traversal": str(root / ".." / "outside"),
            "symlink": str(root / "escape" / "outside"),
            "sibling": str(tmp_path / "backups-other" / "snapshot"),
            "unconfigured": str(root / "app" / "snapshot"),
        }
        plan["snapshots"]["app"]["path"] = paths[escape]
        if escape == "unconfigured":
            state.backup_roots = ()
        before = metadata(state)
        with pytest.raises(ValueError):
            with state.transaction() as conn:
                conn.execute("UPDATE attempts SET plan=?", (json.dumps(plan),))
        assert metadata(state) == before
        state.conn.execute("UPDATE attempts SET plan=?", (json.dumps(plan),))
        with pytest.raises(CorruptStateError):
            State.open(tmp_path, backup_roots=state.backup_roots)
    finally:
        state.close()


@pytest.mark.parametrize("change", [{"extra": True}, {"verified": 1}, {"path": "/" + "x" * 1024}, {"path": 1}])
def test_snapshot_shape_and_bounds(tmp_path, change):
    plan = deepcopy(PLAN)
    plan["snapshots"] = {"app": {"path": str(tmp_path / "snapshot"), "verified": True, **change}}
    with pytest.raises(ValueError):
        validate_frozen_plan(plan, backup_roots=(tmp_path,))


@pytest.mark.parametrize("detail", [
    "token=fake-token-123456789",
    "Authorization: Bearer fake-token-123456789",
    '{"password": "fake-password-123456789"}',
    "'api_key': 'fake-token-123456789'",
    "Bearer fake-token-123456789",
    "bearer=fake-token-123456789",
    "Authorization: Basic fake-token-123456789",
])
def test_event_secrets_never_persist(state, detail):
    before = metadata(state)
    with pytest.raises(ValueError, match="secret"):
        state.add_event(AT, "operator", "deploy", detail)
    with pytest.raises(ValueError):
        with state.transaction() as conn:
            conn.execute("INSERT INTO events(at,actor,action,detail) VALUES(?,?,?,?)", (AT, "operator", "deploy", detail))
    assert metadata(state) == before
    assert b"fake-token-123456789" not in state.path.read_bytes()
    assert b"fake-password-123456789" not in state.path.read_bytes()


def test_event_bounds_and_safe_detail(state):
    state.add_event(AT, "operator", "deploy", "x" * 1024)
    before = metadata(state)
    with pytest.raises(ValueError):
        state.add_event(AT, "operator", "deploy", "x" * 1025)
    assert metadata(state) == before


@contextmanager
def child_process(code, *args):
    child = subprocess.Popen([sys.executable, "-c", code, *map(str, args)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        yield child
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        child.stdout.close()
        child.stderr.close()


def read_marker(child):
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=10), "child did not reach boundary"
    marker = child.stdout.readline().strip()
    assert marker == "ready", f"unexpected child boundary: {marker}"


@pytest.mark.parametrize("field", ["highwater", "rollback_floor"])
def test_counters_compare_after_acquiring_process_lock(state, tmp_path, field):
    # The child announces lock acquisition *before* blocking on the parent's
    # flock. Reading before this point reproduces the original stale-read race.
    code = '''
import sys
from scripts.controller.state import MutationLock, State
s = State.open(sys.argv[1], lock_timeout=10)
enter = MutationLock.__enter__
def announced_enter(self):
    print("ready", flush=True)
    return enter(self)
MutationLock.__enter__ = announced_enter
try:
    s.set_counters(**{sys.argv[2]: 5})
except ValueError:
    print("refused", flush=True)
else:
    raise AssertionError("stale counter update committed")
s.close()
'''
    with ExitStack() as stack:
        with state.mutation():
            child = stack.enter_context(child_process(code, tmp_path, field))
            read_marker(child)
            state.set_counters(**{field: 10})
        output, error = child.communicate(timeout=10)
        assert child.returncode == 0, error
        assert output.strip() == "refused"
    assert state.control()[field] == 10


def test_command_lock_reused_between_helpers_and_handles(state, tmp_path):
    code = '''
import sys, time
from scripts.controller.state import State, ControllerBusyError
s = State.open(sys.argv[1], lock_timeout=.15)
assert s.control()["highwater"] == 6  # Readers take no flock.
start = time.monotonic()
try:
    s.set_counters(highwater=7)
except ControllerBusyError as e:
    assert str(e) == "controller busy"
    assert .1 <= time.monotonic() - start < 2
else:
    raise AssertionError("another command entered")
s.close()
print("ready", flush=True)
'''
    # Even an explicit outer MutationLock must be reused by State helpers.
    with MutationLock(tmp_path):
        with state.mutation(timeout=.01):
            state.set_counters(highwater=5)
            other = State.open(tmp_path, lock_timeout=.01)
            try:
                other.set_counters(highwater=6)
            finally:
                other.close()
        with child_process(code, tmp_path) as child:
            read_marker(child)
            _, error = child.communicate(timeout=10)
            assert child.returncode == 0, error
        state.add_event(AT, "controller", "prepare", "command complete")
    result = subprocess.run([sys.executable, "-c",
                             "import sys; from scripts.controller.state import State; s=State.open(sys.argv[1],lock_timeout=.01); s.set_counters(highwater=7); s.close()",
                             str(tmp_path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert state.control()["highwater"] == 7


def test_command_exception_releases_lock(state, tmp_path):
    with pytest.raises(RuntimeError):
        with state.mutation():
            raise RuntimeError("command failed")
    with child_process("from scripts.controller.state import MutationLock; import sys\nwith MutationLock(sys.argv[1], timeout=.01): print('ready', flush=True)", tmp_path) as child:
        read_marker(child)
        _, error = child.communicate(timeout=10)
        assert child.returncode == 0, error


COMMIT_PROBE = """
import sqlite3, sys
c = sqlite3.connect(sys.argv[1], timeout=0)
try:
    c.execute("SELECT * FROM control").fetchall()
except sqlite3.OperationalError as e:
    if e.sqlite_errorcode != sqlite3.SQLITE_BUSY:
        raise
    sys.exit(0)
sys.exit(1)
"""


KILL_TRANSITION = '''
import json, os, signal, sys
from scripts.controller.state import State
s = State.open(sys.argv[1])
s.conn.execute("PRAGMA busy_timeout=30000")
with s.mutation():
    with s.transaction() as c:
        c.execute("UPDATE releases SET outcome='committed'")
        c.execute("UPDATE attempts SET finished_at=?", (sys.argv[3],))
        c.execute("UPDATE observations SET image_id=?", (sys.argv[4],))
        c.execute("UPDATE events SET detail='transition committed'")
        c.execute("UPDATE control SET phase='IDLE',highwater=5,rollback_floor=3,current_seq=5,active_attempt_id=NULL")
        if sys.argv[2] in ('before', 'during'):
            print('ready', flush=True)
        if sys.argv[2] == 'before':
            os.kill(os.getpid(), signal.SIGSTOP)
    if sys.argv[2] == 'after':
        print('ready', flush=True)
        os.kill(os.getpid(), signal.SIGSTOP)
s.close()
'''


@pytest.mark.parametrize("boundary", ["before", "during", "after"])
def test_sigkill_at_commit_boundaries(state, tmp_path, boundary):
    seed(state)
    with state.transaction() as conn:
        conn.execute("UPDATE control SET phase='VERIFY',active_attempt_id=1")
    old = metadata(state)
    new = deepcopy(old)
    row = list(new["control"][0])
    row[3:7] = ["IDLE", 5, 3, 5]
    row[8] = None
    new["control"] = [tuple(row)]
    for table, column, value in (("releases", 5, "committed"), ("attempts", 7, AT),
                                 ("observations", 3, DIGEST), ("events", 5, "transition committed")):
        row = list(new[table][0])
        row[column] = value
        new[table] = [tuple(row)]
    # A reader's SHARED lock lets BEGIN IMMEDIATE and all updates proceed but
    # blocks COMMIT's upgrade to EXCLUSIVE. A fresh reader becoming SQLITE_BUSY
    # proves SQLite has acquired PENDING *inside* COMMIT, not just traced its SQL.
    reader = sqlite3.connect(state.path, isolation_level=None)
    try:
        if boundary == "during":
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM control").fetchall()
        with child_process(KILL_TRANSITION, tmp_path, boundary, AT, DIGEST) as child:
            read_marker(child)
            deadline = time.monotonic() + 10
            while True:
                assert time.monotonic() < deadline, "commit boundary was not reached"
                assert child.poll() is None, "child exited before kill"
                if boundary == "during":
                    # Use another process: SQLite handles in this process can
                    # reuse the reader's existing POSIX SHARED lock.
                    probe = subprocess.run([sys.executable, "-c", COMMIT_PROBE, str(state.path)],
                                           capture_output=True, text=True, timeout=5)
                    assert probe.returncode in (0, 1), probe.stderr
                    if probe.returncode == 0:
                        break
                else:
                    pid, status = os.waitpid(child.pid, os.WUNTRACED | os.WNOHANG)
                    if pid:
                        assert os.WIFSTOPPED(status)
                        break
                time.sleep(.005)
            os.kill(child.pid, signal.SIGKILL)
            assert child.wait(timeout=10) == -signal.SIGKILL
    finally:
        reader.close()
    reopened = State.open(tmp_path)
    try:
        recovered = metadata(reopened)
        assert recovered in (old, new)
        assert recovered == (new if boundary == "after" else old)
        assert reopened.conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        reopened.close()
