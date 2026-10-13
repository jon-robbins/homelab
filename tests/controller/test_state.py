import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from scripts.controller.state import (
    ControllerBusyError, CorruptStateError, MissingStateError, MutationLock,
    SchemaVersionError, State, StateError, validate_frozen_plan,
)


def initialized(tmp_path):
    return State.init(tmp_path, site_id="site-a", highwater=4, rollback_floor=2)


def test_schema_pragmas_and_init(tmp_path):
    state = initialized(tmp_path)
    assert state.control()["site_id"] == "site-a"
    assert state.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert state.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    assert state.conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    state.close()
    with pytest.raises(StateError, match="already exists"):
        State.init(tmp_path, site_id="site-a", highwater=0, rollback_floor=0)


def test_missing_corrupt_and_wrong_schema_fail_closed(tmp_path):
    with pytest.raises(MissingStateError): State.open(tmp_path)
    state=initialized(tmp_path); state.close()
    db=tmp_path/"controller.sqlite3"
    conn=sqlite3.connect(db); conn.execute("PRAGMA foreign_keys=OFF"); conn.execute("DROP TABLE control"); conn.execute("CREATE TABLE control(id INTEGER PRIMARY KEY, schema INTEGER, site_id TEXT, phase TEXT, highwater INTEGER, rollback_floor INTEGER, current_seq INTEGER, previous_seq INTEGER, active_attempt_id INTEGER, degraded_reason TEXT)"); conn.execute("INSERT INTO control VALUES(1,2,'site-a','IDLE',4,2,NULL,NULL,NULL,NULL)"); conn.commit(); conn.close()
    with pytest.raises(SchemaVersionError): State.open(tmp_path)


@pytest.mark.parametrize("field", ["highwater", "rollback_floor"])
def test_monotonic_counters(tmp_path, field):
    state=initialized(tmp_path)
    before=state.control()
    with pytest.raises(ValueError, match="cannot decrease"):
        state.set_counters(**{field:before[field]-1})
    assert state.control()[field] == before[field]
    state.close()


def test_closed_plan_and_event_redaction(tmp_path):
    plan={"changed_services":[],"service_steps":{},"manual_recovery_required":False,"stopped_for_maintenance":[],"snapshots":{}}
    assert validate_frozen_plan(plan) is plan
    with pytest.raises(ValueError): validate_frozen_plan({**plan,"unexpected":True})
    state=initialized(tmp_path)
    with pytest.raises(ValueError, match="secret"):
        state.add_event("2026-10-13T00:00:00Z", "operator", "deploy", "token=ghp_abcdefghijklmnopqrstuvwxyz")
    assert state.conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    state.close()


def test_mutation_lock_times_out(tmp_path):
    with MutationLock(tmp_path):
        with pytest.raises(ControllerBusyError, match="controller busy"):
            with MutationLock(tmp_path, timeout=.01): pass


@pytest.mark.parametrize("boundary,expected", [("before", "IDLE"), ("during", "IDLE"), ("after", "APPLY")])
def test_kill_safe_transaction_old_or_new(tmp_path, boundary, expected):
    state=initialized(tmp_path); state.close()
    db=tmp_path/"controller.sqlite3"
    # Child commits one complete multi-statement transition; parent kills it at
    # the requested boundary, exercising SQLite's rollback journal recovery.
    code = """import os,sqlite3,signal,sys
c=sqlite3.connect(sys.argv[1],isolation_level=None)
c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=DELETE'); c.execute('PRAGMA synchronous=FULL')
c.execute('BEGIN IMMEDIATE')
if sys.argv[2]=='before': os.kill(os.getpid(),signal.SIGSTOP)
c.execute(\"UPDATE control SET phase='PREPARE',highwater=5 WHERE id=1\")
if sys.argv[2]=='during': os.kill(os.getpid(),signal.SIGSTOP)
c.execute(\"UPDATE control SET phase='APPLY' WHERE id=1\"); c.commit()
if sys.argv[2]=='after': os.kill(os.getpid(),signal.SIGSTOP)
"""
    child=subprocess.Popen([sys.executable,"-c",code,str(db),boundary])
    try:
        os.waitpid(child.pid, os.WUNTRACED)
        os.kill(child.pid, signal.SIGKILL); child.wait()
    finally:
        if child.poll() is None: child.kill(); child.wait()
    reopened=State.open(tmp_path)
    assert reopened.control()["phase"] == expected
    assert reopened.control()["highwater"] == (4 if expected == "IDLE" else 5)
    assert reopened.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    reopened.close()
