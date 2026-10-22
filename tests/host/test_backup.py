from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
BACKUP = ROOT / "host/homelab-backup"


def tools(tmp_path: Path, *, snapshot: str = "nightly-id") -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "restic.log"
    curl_log = tmp_path / "curl.log"
    (bin_dir / "restic").write_text(
        """#!/bin/sh
set -eu
printf '%s\n' --- >> "$FAKE_RESTIC_LOG"
printf '%s\n' "$@" >> "$FAKE_RESTIC_LOG"
case "${1:-}" in
  backup) [ "${FAIL_RESTIC:-}" != 1 ] || exit 7; printf '%s\n' "${SNAPSHOT_OUTPUT:-created new snapshot __SNAPSHOT__}" ;;
  ls) find "$STAGE" -type f ;;
  snapshots) cat "$SNAPSHOTS" ;;
  restore)
    target="$4"
    mkdir -p "$target$SERVICE_ROOT/$SERVICE" "$target$STAGE/svc/$SERVICE"
    printf '%s\n' restored > "$target$SERVICE_ROOT/$SERVICE/restored.txt"
    printf '%s\n' staged > "$target$STAGE/svc/$SERVICE/$RESTORE_REL"
    ;;
esac
""".replace("__SNAPSHOT__", snapshot)
    )
    (bin_dir / "sqlite3").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "curl").write_text(
        """#!/bin/sh
printf '%s\n' --- >> "$FAKE_CURL_LOG"
printf '%s\n' "$@" >> "$FAKE_CURL_LOG"
exit "${CURL_STATUS:-0}"
"""
    )
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_RESTIC_LOG": str(log),
        "FAKE_CURL_LOG": str(curl_log),
        "SNAPSHOTS": str(tmp_path / "snapshots.json"),
        "SNAPSHOT_OUTPUT": f"created new snapshot {snapshot}",
    }
    return log, env


def run(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(script), *args], cwd=ROOT, env=env, text=True, capture_output=True)


def script_for(tmp_path: Path, trust: Path | None = None) -> Path:
    script = tmp_path / "homelab-backup"
    (tmp_path / "state").mkdir(exist_ok=True)
    text = BACKUP.read_text().replace("/var/lib/homelab-backup", str(tmp_path / "state"))
    if trust is not None:
        text = text.replace("/srv/homelab/trust", str(trust))
    script.write_text(text)
    script.chmod(0o755)
    return script


def calls(log: Path) -> list[list[str]]:
    return [block.splitlines() for block in log.read_text().split("---\n") if block.strip()]


def db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("database\n")


def test_nightly_multiroot_stages_and_excludes_live_databases(tmp_path: Path):
    first, second, vpn = (tmp_path / name for name in ("one", "two", "vpn"))
    db(first / "app.db")
    db(second / "other.sqlite")
    db(first / "nested/logs.db")
    db(first / "plex/com.plexapp.plugins.library.blobs.db")
    db(first / "autobrr.backup.old/state.db")
    db(first / "filebrowser/tmp/x.db")
    db(first / "tl-intake-gate/backups/old.sqlite3")
    db(first / "readarr/cache.db")
    db(first / "3x-ui/db/x-ui.db")
    db(first / "3x-ui/exports/x.db")
    db(first / "overseerr-pr1918/db.sqlite3")
    db(vpn / "vpn.db")
    stage = tmp_path / "stage"
    log, env = tools(tmp_path)
    env.update(DATA_ROOTS=f"{first} {second}", VPN_ROOT=str(vpn), STAGE=str(stage), REPO=str(tmp_path / "repo"))
    result = run(script_for(tmp_path), env)
    assert result.returncode == 0, result.stderr
    backup = calls(log)[0]
    assert {str(first), str(second), str(vpn), str(stage)} <= set(backup)
    for root in (first, second, vpn):
        for ext in ("db", "db-wal", "db-shm", "sqlite", "sqlite-wal", "sqlite-shm", "sqlite3", "sqlite3-wal", "sqlite3-shm"):
            assert f"{root}/**/*.{ext}" in backup
    assert (stage / "0/app.db").read_text() == "database\n"
    assert (stage / "1/other.sqlite").read_text() == "database\n"
    assert (stage / "srv/vpn/vpn.db").read_text() == "database\n"
    assert not (stage / "0/nested/logs.db").exists()
    assert not (stage / "0/plex/com.plexapp.plugins.library.blobs.db").exists()
    assert not (stage / "0/autobrr.backup.old/state.db").exists()
    for rel in ("filebrowser/tmp/x.db", "tl-intake-gate/backups/old.sqlite3", "readarr/cache.db", "3x-ui/db/x-ui.db", "3x-ui/exports/x.db", "overseerr-pr1918/db.sqlite3"):
        assert not (stage / "0" / rel).exists()


def test_dry_run_prints_arguments_and_staged_database_list_without_calls(tmp_path: Path):
    root = tmp_path / "data"
    db(root / "app.db")
    stage = tmp_path / "stage"
    log, env = tools(tmp_path)
    env.update(DATA_ROOTS=str(root), VPN_ROOT=str(tmp_path / "missing-vpn"), STAGE=str(stage))
    result = run(script_for(tmp_path), env, "--dry-run")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert {str(root), str(stage), str(stage / "0/app.db")} <= set(lines)
    assert not log.exists() and not stage.exists()


def test_service_prints_snapshot_id_last_and_uses_both_tags(tmp_path: Path):
    service_root = tmp_path / "data"
    db(service_root / "plex" / "library.db")
    stage = tmp_path / "stage"
    log, env = tools(tmp_path, snapshot="service-id")
    env.update(SERVICE_ROOT=str(service_root), STAGE=str(stage))
    result = run(script_for(tmp_path), env, "--service", "plex", "--tag", "weekly")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "service-id"
    backup = calls(log)[0]
    assert backup[0] == "backup"
    assert ["--tag", "weekly"] == backup[1:3]
    assert ["--tag", "service:plex"] == backup[3:5]
    assert str(service_root / "plex") in backup and str(stage) in backup
    assert (stage / "svc/plex/library.db").exists()
    assert len(calls(log)) == 1


def test_service_without_its_data_dir_fails_before_calling_restic(tmp_path: Path):
    service_root = tmp_path / "data"
    service_root.mkdir()
    log, env = tools(tmp_path)
    env.update(SERVICE_ROOT=str(service_root), STAGE=str(tmp_path / "stage"))
    result = run(script_for(tmp_path), env, "--service", "plex", "--tag", "weekly")
    assert result.returncode == 1
    assert f"homelab-backup: no data dir {service_root / 'plex'}" in result.stderr
    assert not log.exists()  # restic never ran


def test_restore_moves_old_service_and_restores_staged_database(tmp_path: Path):
    service_root = tmp_path / "data"
    old = service_root / "plex"
    db(old / "library.db")
    stage = tmp_path / "stage"
    (tmp_path / "snapshots.json").write_text('[{"id":"restore-id"}]\n')
    log, env = tools(tmp_path)
    env.update(SERVICE_ROOT=str(service_root), STAGE=str(stage), SERVICE="plex", RESTORE_REL="library.db")
    result = run(script_for(tmp_path), env, "--restore", "plex", "--tag", "weekly")
    assert result.returncode == 0, result.stderr
    assert (old / "restored.txt").read_text() == "restored\n"
    assert (old / "library.db").read_text() == "staged\n"
    previous = list(service_root.glob("plex.pre-restore-*"))
    assert len(previous) == 1 and (previous[0] / "library.db").read_text() == "database\n"
    assert calls(log)[0][:5] == ["snapshots", "--latest", "1", "--tag", "weekly"]


def test_restore_without_matching_snapshot_fails(tmp_path: Path):
    service_root = tmp_path / "data"
    old = service_root / "plex"
    db(old / "library.db")
    (tmp_path / "snapshots.json").write_text("[]\n")
    log, env = tools(tmp_path)
    env.update(SERVICE_ROOT=str(service_root), STAGE=str(tmp_path / "stage"), SERVICE="plex")
    result = run(script_for(tmp_path), env, "--restore", "plex", "--tag", "weekly")
    assert result.returncode == 1
    assert old.exists() and not list(service_root.glob("plex.pre-restore-*"))
    assert len(calls(log)) == 1


def test_nightly_failure_pings_deadman_and_missing_file_does_not(tmp_path: Path):
    trust = tmp_path / "trust"
    trust.mkdir()
    (trust / "deadman.env").write_text("BACKUP_PING_URL=https://deadman.example/ping\n")
    script = script_for(tmp_path, trust)
    log, env = tools(tmp_path)
    env.update(DATA_ROOTS=str(tmp_path / "data"), VPN_ROOT=str(tmp_path / "missing-vpn"), STAGE=str(tmp_path / "stage"), FAIL_RESTIC="1")
    result = run(script, env)
    assert result.returncode != 0
    assert (tmp_path / "curl.log").read_text().splitlines()[-1] == "https://deadman.example/ping/fail"

    (tmp_path / "curl.log").unlink()
    env.pop("FAIL_RESTIC")
    env["BACKUP_PING_URL"] = "https://must-not-be-used.example"
    (tmp_path / "data").mkdir()
    result = run(script_for(tmp_path), env)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "curl.log").exists()


def test_nightly_slims_staged_intake_database(tmp_path: Path):
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 is unavailable")
    root, stage = tmp_path / "data", tmp_path / "stage"
    live = root / "tl-intake-gate/intake.sqlite3"
    live.parent.mkdir(parents=True)
    with sqlite3.connect(live) as conn:
        conn.executescript(
            "CREATE TABLE outcome_snapshots (x); INSERT INTO outcome_snapshots VALUES (1);"
            "CREATE TABLE decisions (decided_at INTEGER);"
            "INSERT INTO decisions VALUES (0), (strftime('%s','now'));"
        )
    log, env = tools(tmp_path)
    (tmp_path / "bin/sqlite3").unlink()
    env.update(DATA_ROOTS=str(root), VPN_ROOT=str(tmp_path / "vpn"), STAGE=str(stage), REPO=str(tmp_path / "repo"))
    result = run(script_for(tmp_path), env)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(stage / "0/tl-intake-gate/intake.sqlite3") as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"decisions"}
        assert conn.execute("SELECT count(*) FROM decisions").fetchone() == (1,)
    with sqlite3.connect(live) as conn:
        assert conn.execute("SELECT count(*) FROM outcome_snapshots").fetchone() == (1,)
