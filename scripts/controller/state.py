"""SQLite backed, fail-closed controller state (schema v1)."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import threading
from typing import Any, Iterator

from scripts.release import contracts


class StateError(RuntimeError):
    """Base class for state errors."""


class MissingStateError(StateError): pass
class CorruptStateError(StateError): pass
class SchemaVersionError(StateError): pass
class ControllerBusyError(StateError): pass


PHASES = ("IDLE", "PREPARE", "APPLY", "VERIFY", "FAIL", "DEGRADED")
# Reject sensitive field names even when quoted (JSON), and authorization
# schemes and command options whose credentials follow whitespace.
SECRET_PATTERN = re.compile(
    r"(?i)(?:token|secret|password|credential|api[_-]?key|authorization|bearer)"
    r"[\"']?\s*[:=]|\b(?:bearer|basic)\s+\S+|"
    r"(?:^|\s)--(?:token|secret|password|credential|api[_-]?key|authorization)\s+\S+|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,})\b|"
    r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@"  # user:password@ in a URL
)
_LOCKS = threading.local()


SCHEMA = """
CREATE TABLE control (
 id INTEGER PRIMARY KEY CHECK(id = 1), schema INTEGER NOT NULL CHECK(schema = 1),
 site_id TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('IDLE','PREPARE','APPLY','VERIFY','FAIL','DEGRADED')),
 highwater INTEGER NOT NULL CHECK(highwater >= 0), rollback_floor INTEGER NOT NULL CHECK(rollback_floor >= 0),
 current_seq INTEGER REFERENCES releases(sequence), previous_seq INTEGER REFERENCES releases(sequence),
 active_attempt_id INTEGER REFERENCES attempts(id), degraded_reason TEXT CHECK(degraded_reason IS NULL OR length(degraded_reason) <= 1024)
);
CREATE TABLE releases (
 sequence INTEGER PRIMARY KEY CHECK(sequence > 0), record_sha256 TEXT NOT NULL UNIQUE, record_bytes BLOB NOT NULL,
 signature_bytes BLOB NOT NULL, bundle_sha256 TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('pending','committed','failed','skipped')), images TEXT NOT NULL
);
CREATE TABLE attempts (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('deploy','secret-activation','recovery')),
 release_seq INTEGER REFERENCES releases(sequence), baseline_seq INTEGER REFERENCES releases(sequence), plan TEXT NOT NULL,
 recovery_action TEXT NOT NULL CHECK(recovery_action IN ('none','restore-baseline','manual')),
 started_at TEXT NOT NULL, finished_at TEXT, failure TEXT CHECK(failure IS NULL OR length(failure) <= 1024)
);
CREATE TABLE observations (
 service TEXT PRIMARY KEY, observed_at TEXT NOT NULL, container_id TEXT, image_id TEXT,
 spec_matches INTEGER NOT NULL CHECK(spec_matches IN (0,1)), health TEXT NOT NULL CHECK(health IN ('pass','fail','error')),
 semantic TEXT NOT NULL CHECK(semantic IN ('pass','fail','error')), loaded_secrets TEXT NOT NULL
);
CREATE TABLE events (
 id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('controller','operator')),
 action TEXT NOT NULL, attempt_id INTEGER REFERENCES attempts(id), detail TEXT NOT NULL CHECK(length(detail) <= 1024)
);
"""


def _obj(v: Any, required: set[str], optional: set[str] = set()) -> dict:
    if not isinstance(v, dict) or set(v) - required - optional or required - set(v):
        raise ValueError("invalid closed record")
    return v

def _bool(v: Any) -> bool:
    if type(v) is not bool: raise ValueError("expected boolean")
    return v

def _str(v: Any, limit: int = 1024) -> str:
    if not isinstance(v, str) or len(v) > limit: raise ValueError("expected bounded string")
    return v

def _image_map(v: Any) -> dict:
    if not isinstance(v, dict): raise ValueError("expected images map")
    for name, image in v.items():
        contracts.validate_name(name)
        item = _obj(image, {"ref", "local_image_id"})
        contracts.validate_image_ref(item["ref"])
        if item["local_image_id"] is not None: contracts.validate_digest(item["local_image_id"])
    return v

def _stamp(v: Any) -> dict:
    v = _obj(v, {"device", "inode", "size", "mtime_ns"})
    for key in v: contracts.validate_nonnegative_integer(v[key])
    return v

def _fingerprint(v: Any) -> dict:
    fields = {"image_manifest", "local_image_id", "bundle_sha256", "model_manifest_sha256", "fixture_sha256", "gpu_uuid", "kernel_release", "boot_id", "nvidia_driver_version", "nvidia_container_runtime_version", "cuda_runtime_version", "trusted_profile_sha256"}
    v = _obj(v, fields)
    contracts.validate_image_ref(v["image_manifest"])
    for k in ("local_image_id", "bundle_sha256", "fixture_sha256", "trusted_profile_sha256"): contracts.validate_digest(v[k])
    if v["model_manifest_sha256"] is not None: contracts.validate_digest(v["model_manifest_sha256"])
    for k in fields - {"image_manifest", "local_image_id", "bundle_sha256", "model_manifest_sha256", "fixture_sha256", "trusted_profile_sha256"}: _str(v[k], 256)
    return v

def _backup_roots(roots) -> tuple[Path, ...]:
    resolved = []
    for root in roots:
        root = Path(root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("backup roots must be installed absolute directories")
        resolved.append(root.resolve(strict=True))
    return tuple(resolved)


def validate_frozen_plan(v: Any, *, backup_roots: tuple[Path, ...] = ()) -> dict:
    v = _obj(v, {"changed_services", "service_steps", "manual_recovery_required", "stopped_for_maintenance", "snapshots"}, {"maintenance_permission", "qualifier"})
    for key in ("changed_services", "stopped_for_maintenance"):
        if not isinstance(v[key], list): raise ValueError("expected Name[]")
        for name in v[key]: contracts.validate_name(name)
    _bool(v["manual_recovery_required"])
    if not isinstance(v["service_steps"], dict): raise ValueError("expected service_steps map")
    for name, step in v["service_steps"].items():
        contracts.validate_name(name); step = _obj(step, {"operation", "may_have_run"})
        if step["operation"] not in {"create", "replace", "remove"}: raise ValueError("invalid operation")
        _bool(step["may_have_run"])
    if not isinstance(v["snapshots"], dict): raise ValueError("expected snapshots map")
    for name, snap in v["snapshots"].items():
        contracts.validate_name(name); snap = _obj(snap, {"path", "verified"})
        p = snap["path"]
        if not isinstance(p, str) or not os.path.isabs(p) or len(p) > 1024: raise ValueError("expected absolute approved backup path")
        resolved = Path(p).resolve()
        if not any(resolved.is_relative_to(root) for root in _backup_roots(backup_roots)):
            raise ValueError("snapshot path is outside approved backup roots")
        _bool(snap["verified"])
    if "maintenance_permission" in v:
        m = _obj(v["maintenance_permission"], {"record_sha256", "participants", "expires_at", "consumed"})
        contracts.validate_digest(m["record_sha256"])
        if not isinstance(m["participants"], list): raise ValueError("expected participants")
        for n in m["participants"]: contracts.validate_name(n)
        contracts.validate_timestamp(m["expires_at"]); _bool(m["consumed"])
    if "qualifier" in v:
        q = _obj(v["qualifier"], {"profile", "container_names", "hardware_and_inputs", "result", "baseline_restored"})
        contracts.validate_name(q["profile"])
        if not isinstance(q["container_names"], list): raise ValueError("expected container_names")
        for n in q["container_names"]: contracts.validate_name(n)
        _fingerprint(q["hardware_and_inputs"])
        if q["result"] not in {"pending", "pass", "fail", "interrupted"}: raise ValueError("invalid qualifier result")
        _bool(q["baseline_restored"])
    return v

def _json(value: Any, validator) -> str:
    validator(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))

class MutationLock:
    """Reentrant within a command's thread; exclusive across processes/threads.

    State helpers reuse the held flock, including when the command opens another
    State handle. The PID in the key prevents a fork from reusing a parent's lock.
    """

    def __init__(self, state_dir: str | Path, timeout: float = 3.0):
        if timeout < 0 or not math.isfinite(timeout):
            raise ValueError("lock timeout must be finite and nonnegative")
        self.path, self.timeout = Path(state_dir).resolve() / "lock", timeout

    def __enter__(self):
        held = getattr(_LOCKS, "held", None)
        if held is None:
            held = _LOCKS.held = {}
        key = (os.getpid(), self.path)
        if key in held:
            held[key][1] += 1
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held[key] = [fd, 1]
                    return self
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ControllerBusyError("controller busy") from None
                    time.sleep(min(.05, max(0, deadline - time.monotonic())))
        except BaseException:
            os.close(fd)
            raise

    def __exit__(self, *_):
        key = (os.getpid(), self.path)
        entry = _LOCKS.held[key]
        entry[1] -= 1
        if entry[1] == 0:
            del _LOCKS.held[key]
            try:
                fcntl.flock(entry[0], fcntl.LOCK_UN)
            finally:
                os.close(entry[0])


class State:
    """Durable metadata with backup roots supplied by trusted host configuration.

    An empty root allowlist authorizes no snapshot paths. It is never populated
    from release records or from the plan being validated.
    """

    def __init__(self, path: str | Path, conn: sqlite3.Connection, *, backup_roots=(), lock_timeout: float = 3.0):
        self.path, self.conn = Path(path), conn
        self.backup_roots = _backup_roots(backup_roots)
        self.lock_timeout = lock_timeout

    def validate_plan(self, value: Any) -> dict:
        return validate_frozen_plan(value, backup_roots=self.backup_roots)

    @contextmanager
    def mutation(self, *, timeout: float | None = None) -> Iterator["State"]:
        """Hold for the whole mutating command, across all helper transactions.

        Use ``with state.mutation():`` around inspection, metadata changes and
        any intervening command work. Standalone helpers also acquire this lock.
        """
        with MutationLock(self.path.parent, self.lock_timeout if timeout is None else timeout):
            yield self

    @classmethod
    def init(cls, state_dir: str | Path, *, site_id: str, highwater: int, rollback_floor: int, lock_timeout: float = 3.0, backup_roots=()) -> "State":
        backup_roots = _backup_roots(backup_roots)
        d = Path(state_dir); d.mkdir(parents=True, exist_ok=True); path = d / "controller.sqlite3"
        contracts.validate_name(site_id); contracts.validate_nonnegative_integer(highwater); contracts.validate_nonnegative_integer(rollback_floor)
        with MutationLock(d, lock_timeout):
            if path.exists(): raise StateError("state database already exists")
            conn = _connect(path)
            try:
                conn.executescript(SCHEMA)
                conn.execute("INSERT INTO control(id,schema,site_id,phase,highwater,rollback_floor) VALUES(1,1,?,'IDLE',?,?)", (site_id, highwater, rollback_floor))
                conn.commit()
            finally: conn.close()
        return cls.open(d, backup_roots=backup_roots, lock_timeout=lock_timeout)
    @classmethod
    def open(cls, state_dir: str | Path, *, backup_roots=(), lock_timeout: float = 3.0) -> "State":
        d = Path(state_dir); path = d / "controller.sqlite3"
        if not path.exists(): raise MissingStateError("controller state database is missing")
        conn = None
        try:
            conn = _connect(path, existing=True, timeout=lock_timeout)
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok": raise CorruptStateError("controller state integrity check failed")
            row = conn.execute("SELECT schema FROM control WHERE id=1").fetchone()
            if row is None: raise CorruptStateError("controller control row is missing")
            if row[0] != 1: raise SchemaVersionError("unsupported controller schema version")
            result = cls(path, conn, backup_roots=backup_roots, lock_timeout=lock_timeout)
            result.validate_rows()
            return result
        except sqlite3.OperationalError as e:
            if conn is not None: conn.close()
            if _is_busy(e): raise ControllerBusyError("controller busy") from e
            raise CorruptStateError("controller state database is corrupt") from e
        except sqlite3.DatabaseError as e:
            if conn is not None: conn.close()
            raise CorruptStateError("controller state database is corrupt") from e
        except Exception:
            if conn is not None: conn.close()
            raise
    def close(self): self.conn.close()
    def validate_rows(self) -> None:
        if self.conn.execute("SELECT id,schema FROM control").fetchall() != [(1, 1)]:
            raise CorruptStateError("invalid control singleton or schema")
        if self.conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise CorruptStateError("invalid state references")
        c = self.control()
        try:
            contracts.validate_name(c["site_id"])
            if c["phase"] not in PHASES: raise ValueError()
            contracts.validate_nonnegative_integer(c["highwater"]); contracts.validate_nonnegative_integer(c["rollback_floor"])
            _str(c["degraded_reason"]) if c["degraded_reason"] is not None else None
        except Exception as e: raise CorruptStateError("invalid control row") from e
        for row in self.conn.execute("SELECT sequence,record_sha256,record_bytes,signature_bytes,bundle_sha256,outcome,images FROM releases"):
            try:
                contracts.validate_sequence(row[0]); contracts.validate_digest(row[1]); contracts.validate_digest(row[4])
                if not isinstance(row[2], bytes) or not isinstance(row[3], bytes) or row[5] not in {"pending", "committed", "failed", "skipped"}: raise ValueError()
                _image_map(contracts.parse_json(row[6]))
            except Exception as e: raise CorruptStateError("invalid release images JSON") from e
        for row in self.conn.execute("SELECT id,kind,release_seq,baseline_seq,plan,recovery_action,started_at,finished_at,failure FROM attempts"):
            try:
                contracts.validate_positive_integer(row[0])
                if row[1] not in {"deploy","secret-activation","recovery"} or row[5] not in {"none","restore-baseline","manual"}: raise ValueError()
                contracts.validate_timestamp(row[6]); contracts.validate_timestamp(row[7]) if row[7] is not None else None
                _str(row[8]) if row[8] is not None else None
                self.validate_plan(contracts.parse_json(row[4]))
            except Exception as e: raise CorruptStateError("invalid attempt plan JSON") from e
        for row in self.conn.execute("SELECT service,observed_at,container_id,image_id,spec_matches,health,semantic,loaded_secrets FROM observations"):
            try:
                contracts.validate_name(row[0]); contracts.validate_timestamp(row[1]); _str(row[2]) if row[2] is not None else None
                contracts.validate_digest(row[3]) if row[3] is not None else None
                if row[4] not in (0,1) or row[5] not in {"pass","fail","error"} or row[6] not in {"pass","fail","error"}: raise ValueError()
                m=contracts.parse_json(row[7]);
                if not isinstance(m, dict): raise ValueError()
                for name, stamp in m.items(): contracts.validate_secret_name(name); _stamp(stamp)
            except Exception as e: raise CorruptStateError("invalid loaded_secrets JSON") from e
        for row in self.conn.execute("SELECT at,actor,action,detail FROM events"):
            try:
                contracts.validate_timestamp(row[0]); contracts.validate_name(row[2])
                if row[1] not in {"controller", "operator"}: raise ValueError()
                _event_detail(row[3])
            except Exception as e: raise CorruptStateError("invalid event detail") from e

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit a complete validated transition, or roll back every statement."""
        with self.mutation():
            try:
                self.conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                if _is_busy(e): raise ControllerBusyError("controller busy") from e
                raise
            try:
                old = self.control()
                yield self.conn
                try:
                    self.validate_rows()
                except CorruptStateError as e:
                    raise ValueError("invalid state transition") from e
                new = self.control()
                for key in ("highwater", "rollback_floor"):
                    if new[key] < old[key]: raise ValueError(f"{key} cannot decrease")
                self._require_staged_images(new)
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def _require_staged_images(self, control: dict[str, Any]) -> None:
        """Section 4: every required image has a local_image_id before APPLY or any maintenance stop."""
        if control["active_attempt_id"] is None: return
        release_seq, plan = self.conn.execute("SELECT release_seq,plan FROM attempts WHERE id=?", (control["active_attempt_id"],)).fetchone()
        if release_seq is None: return
        if control["phase"] not in ("APPLY", "VERIFY") and not contracts.parse_json(plan)["stopped_for_maintenance"]: return
        images = contracts.parse_json(self.conn.execute("SELECT images FROM releases WHERE sequence=?", (release_seq,)).fetchone()[0])
        unstaged = sorted(name for name, image in images.items() if image["local_image_id"] is None)
        if unstaged: raise ValueError(f"image not staged before APPLY or maintenance stop: {unstaged[0]}")
    def control(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT site_id,phase,highwater,rollback_floor,current_seq,previous_seq,active_attempt_id,degraded_reason FROM control WHERE id=1").fetchone()
        if not row: raise CorruptStateError("controller control row is missing")
        return dict(zip(("site_id","phase","highwater","rollback_floor","current_seq","previous_seq","active_attempt_id","degraded_reason"), row))
    def set_counters(self, *, highwater: int | None = None, rollback_floor: int | None = None) -> None:
        with self.transaction():
            old = self.control()
            for key, value in (("highwater", highwater), ("rollback_floor", rollback_floor)):
                if value is not None:
                    contracts.validate_nonnegative_integer(value)
                    if value < old[key]: raise ValueError(f"{key} cannot decrease")
            self.conn.execute("UPDATE control SET highwater=COALESCE(?,highwater),rollback_floor=COALESCE(?,rollback_floor) WHERE id=1", (highwater, rollback_floor))
    def add_release(self, sequence: int, record_sha256: str, record_bytes: bytes, signature_bytes: bytes, bundle_sha256: str, outcome: str, images: dict) -> None:
        contracts.validate_sequence(sequence); contracts.validate_digest(record_sha256); contracts.validate_digest(bundle_sha256); _image_map(images)
        if not isinstance(record_bytes, bytes) or not isinstance(signature_bytes, bytes): raise ValueError("record and signature must be bytes")
        if outcome not in {"pending","committed","failed","skipped"}: raise ValueError("invalid outcome")
        with self.transaction(): self.conn.execute("INSERT INTO releases VALUES(?,?,?,?,?,?,?)", (sequence,record_sha256,record_bytes,signature_bytes,bundle_sha256,outcome,_json(images,_image_map)))
    def add_attempt(self, *, kind: str, release_seq: int | None, baseline_seq: int | None, plan: dict, recovery_action: str, started_at: str, finished_at: str | None = None, failure: str | None = None) -> int:
        if kind not in {"deploy","secret-activation","recovery"} or recovery_action not in {"none","restore-baseline","manual"}: raise ValueError("invalid attempt enum")
        contracts.validate_sequence(release_seq) if release_seq is not None else None
        contracts.validate_sequence(baseline_seq) if baseline_seq is not None else None
        contracts.validate_timestamp(started_at); contracts.validate_timestamp(finished_at) if finished_at is not None else None
        _str(failure) if failure is not None else None
        with self.transaction():
            cur=self.conn.execute("INSERT INTO attempts(kind,release_seq,baseline_seq,plan,recovery_action,started_at,finished_at,failure) VALUES(?,?,?,?,?,?,?,?)", (kind,release_seq,baseline_seq,_json(plan,self.validate_plan),recovery_action,started_at,finished_at,failure))
            return int(cur.lastrowid)
    def add_observation(self, *, service: str, observed_at: str, container_id: str | None, image_id: str | None, spec_matches: bool, health: str, semantic: str, loaded_secrets: dict) -> None:
        contracts.validate_name(service); contracts.validate_timestamp(observed_at); _str(container_id) if container_id is not None else None
        contracts.validate_digest(image_id) if image_id is not None else None
        if type(spec_matches) is not bool or health not in {"pass","fail","error"} or semantic not in {"pass","fail","error"}: raise ValueError("invalid observation")
        if not isinstance(loaded_secrets,dict): raise ValueError("expected loaded secrets map")
        for name, stamp in loaded_secrets.items(): contracts.validate_secret_name(name); _stamp(stamp)
        with self.transaction(): self.conn.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)", (service,observed_at,container_id,image_id,int(spec_matches),health,semantic,_json(loaded_secrets,lambda _: None)))
    def add_event(self, at: str, actor: str, action: str, detail: str, attempt_id: int | None = None) -> None:
        contracts.validate_timestamp(at); contracts.validate_name(action); _event_detail(detail)
        if actor not in {"controller","operator"}: raise ValueError("invalid actor")
        with self.transaction(): self.conn.execute("INSERT INTO events(at,actor,action,attempt_id,detail) VALUES(?,?,?,?,?)", (at,actor,action,attempt_id,detail))

def _event_detail(detail: Any) -> str:
    _str(detail)
    if SECRET_PATTERN.search(detail):
        raise ValueError("event detail appears to contain a secret")
    return detail


def _is_busy(error: sqlite3.OperationalError) -> bool:
    return "locked" in str(error) or "busy" in str(error)


def _connect(path: Path, *, existing: bool = False, timeout: float = 3.0) -> sqlite3.Connection:
    # mode=rw prevents recreation if an expected database disappears during open.
    target = path.resolve().as_uri() + "?mode=rw" if existing else str(path)
    conn = sqlite3.connect(target, isolation_level=None, uri=existing, timeout=timeout)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        return conn
    except BaseException:
        conn.close()
        raise
