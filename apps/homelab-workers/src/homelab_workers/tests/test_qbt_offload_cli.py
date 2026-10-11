import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from homelab_workers.cli import qbt_offload
from homelab_workers.shared.qbt_offload_state import read_qbt_offload_status


class FakeQbt:
    def __init__(self, *_args) -> None:
        self.rows = [{"hash": "a" * 40}]

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def torrents_info_raw(self):
        return self.rows


def test_offload_settings_read_from_yaml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "workers.yaml"
    status_path = tmp_path / "offload-status.json"
    config_path.write_text(
        f"""
offload:
  catchup_seconds: 111
  min_hdd_free_bytes: 222
  status_max_age_seconds: 333
  status_path: {status_path}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKERS_CONFIG_PATH", str(config_path))
    for key in (
        "QBT_OFFLOAD_CATCHUP_SECONDS",
        "QBT_OFFLOAD_MIN_HDD_FREE_BYTES",
        "QBT_OFFLOAD_STATUS_MAX_AGE_SECONDS",
        "QBT_OFFLOAD_STATUS_PATH",
    ):
        monkeypatch.delenv(key, raising=False)

    assert qbt_offload._env_int("QBT_OFFLOAD_CATCHUP_SECONDS", 3600) == 111
    assert qbt_offload._env_int("QBT_OFFLOAD_MIN_HDD_FREE_BYTES", 10_737_418_240) == 222
    assert qbt_offload._env_int("QBT_OFFLOAD_STATUS_MAX_AGE_SECONDS", 7200) == 333
    assert qbt_offload._status_path() == str(status_path)


def test_catch_up_writes_hdd_full_status(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "status.json"
    monkeypatch.setenv("QBT_OFFLOAD_STATUS_PATH", str(status_path))
    monkeypatch.setattr(qbt_offload, "QBittorrentClient", FakeQbt)
    monkeypatch.setattr(
        qbt_offload,
        "list_offload_candidates",
        lambda *_args, **_kwargs: [{"hash": "a" * 40}],
    )
    monkeypatch.setattr(
        qbt_offload,
        "run_offload_moves",
        lambda *_args, **_kwargs: [{"ok": False, "blocked_reason": "hdd_full"}],
    )

    assert qbt_offload.run_catch_up(limit=20) == 0

    status = read_qbt_offload_status(status_path)
    assert status is not None
    assert status["offloaded"] == 0
    assert status["failed"] == 0
    assert status["hdd_full"] is True
    assert status["pending_on_nvme"] == 1


def test_run_candidates_passes_explicit_hdd_free_space_mount(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "status.json"
    hdd_mount = tmp_path / "hdd"
    captured: dict[str, object] = {}
    monkeypatch.setenv("QBT_OFFLOAD_STATUS_PATH", str(status_path))
    monkeypatch.setattr(qbt_offload, "QBittorrentClient", FakeQbt)

    def record_run(_qbt, _candidates, **kwargs):
        captured.update(kwargs)
        return [{"ok": True}]

    monkeypatch.setattr(qbt_offload, "run_offload_moves", record_run)

    result = qbt_offload._run_candidates(
        [{"hash": "a" * 40}],
        pending_count=1,
        hdd_mount=str(hdd_mount),
    )

    assert result == 0
    assert captured["free_space_mount"] == str(hdd_mount)


def test_offload_hash_succeeds_when_already_offloaded(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "status.json"
    monkeypatch.setenv("QBT_OFFLOAD_STATUS_PATH", str(status_path))
    monkeypatch.setattr(qbt_offload, "QBittorrentClient", FakeQbt)
    monkeypatch.setattr(
        FakeQbt,
        "torrents_info_raw",
        lambda _self: [
            {
                "hash": "a" * 40,
                "progress": 1.0,
                "content_path": "/seeding/Show.S01",
            }
        ],
    )
    monkeypatch.setattr(
        qbt_offload,
        "plan_offload_candidate",
        lambda _torrent, **_kwargs: None,
    )

    assert qbt_offload.run_offload_hash("a" * 40) == 0

    status = read_qbt_offload_status(status_path)
    assert status is not None
    assert status["offloaded"] == 0
    assert status["failed"] == 0
    assert qbt_offload.evaluate_qbt_offload_health(
        status, max_age_seconds=7200, now=status["updated_at"]
    )["healthy"] is True


def test_healthcheck_uses_status_file(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "status.json"
    monkeypatch.setenv("QBT_OFFLOAD_STATUS_PATH", str(status_path))
    qbt_offload.write_qbt_offload_status(
        status_path,
        {
            "updated_at": 100,
            "last_run": 100,
            "offloaded": 0,
            "failed": 0,
            "hdd_full": True,
            "pending_on_nvme": 1,
        },
    )
    monkeypatch.setattr(qbt_offload.time, "time", lambda: 100)

    assert qbt_offload.healthcheck() == 1


def test_http_hook_offloads_requested_hash(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(qbt_offload, "run_offload_hash", lambda value: calls.append(value) or 0)
    monkeypatch.setattr(
        qbt_offload,
        "health_payload",
        lambda: (200, {"healthy": True, "reason": "ok"}),
    )
    server = qbt_offload.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/offload",
            data=json.dumps({"hash": "a" * 40}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"ok": True}
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert calls == ["a" * 40]


def test_catch_up_holds_lock_during_candidate_snapshot(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "status.json"
    monkeypatch.setenv("QBT_OFFLOAD_STATUS_PATH", str(status_path))
    monkeypatch.setattr(qbt_offload, "QBittorrentClient", FakeQbt)

    listing_started = threading.Event()
    release_listing = threading.Event()

    def slow_list(*_args, **_kwargs):
        listing_started.set()
        release_listing.wait(timeout=5)
        return [{"hash": "a" * 40}]

    monkeypatch.setattr(qbt_offload, "list_offload_candidates", slow_list)
    monkeypatch.setattr(
        qbt_offload,
        "run_offload_moves",
        lambda *_args, **_kwargs: [{"ok": True}],
    )

    catch_up_done = threading.Event()

    def run_catch_up_thread() -> None:
        try:
            qbt_offload.run_catch_up(limit=20)
        finally:
            catch_up_done.set()

    thread = threading.Thread(target=run_catch_up_thread)
    thread.start()
    assert listing_started.wait(timeout=5)

    lock_acquired = threading.Event()

    def try_acquire_lock() -> None:
        with qbt_offload._OFFLOAD_STATUS_LOCK:
            lock_acquired.set()

    lock_thread = threading.Thread(target=try_acquire_lock)
    lock_thread.start()
    time.sleep(0.1)
    assert not lock_acquired.is_set()

    release_listing.set()
    assert lock_acquired.wait(timeout=5)
    catch_up_done.wait(timeout=5)
    lock_thread.join(timeout=5)
    thread.join(timeout=5)


def test_http_hook_reports_hdd_full_as_blocked(monkeypatch) -> None:
    monkeypatch.setattr(qbt_offload, "run_offload_hash", lambda _value: 0)
    monkeypatch.setattr(
        qbt_offload,
        "health_payload",
        lambda: (503, {"healthy": False, "reason": "hdd_full"}),
    )
    server = qbt_offload.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/offload",
            data=json.dumps({"hash": "a" * 40}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request)
        except HTTPError as error:
            try:
                assert error.code == 503
                assert json.loads(error.read()) == {
                    "blocked": True,
                    "error": "hdd_full",
                    "ok": False,
                }
            finally:
                error.close()
        else:
            raise AssertionError("expected hdd_full offload to be rejected")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_offload_exclusions_read_native_yaml_lists(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "workers.yaml"
    config_path.write_text(
        """
offload:
  excluded_tags:
    - private
    - ""
    - " keep "
  excluded_categories:
    - archive
    - ""
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKERS_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("QBT_OFFLOAD_EXCLUDED_TAGS", raising=False)
    monkeypatch.delenv("QBT_OFFLOAD_EXCLUDED_CATEGORIES", raising=False)

    assert qbt_offload._offload_exclusions() == (["private", "keep"], ["archive"])


def test_offload_exclusions_read_comma_separated_environment_values(monkeypatch) -> None:
    monkeypatch.setenv("WORKERS_CONFIG_PATH", "/missing/workers.yaml")
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_TAGS", "private, keep, ,")
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_CATEGORIES", "archive,manual")

    assert qbt_offload._offload_exclusions() == (["private", "keep"], ["archive", "manual"])


def test_nonempty_exclusion_environment_values_override_yaml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "workers.yaml"
    config_path.write_text(
        """
offload:
  excluded_tags: yaml-tag
  excluded_categories: yaml-category
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKERS_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_TAGS", "environment-tag")
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_CATEGORIES", "environment-category")

    assert qbt_offload._offload_exclusions() == (["environment-tag"], ["environment-category"])


def test_blank_exclusion_environment_values_yield_to_yaml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "workers.yaml"
    config_path.write_text(
        """
offload:
  excluded_tags: yaml-tag
  excluded_categories: yaml-category
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKERS_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_TAGS", " ")
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_CATEGORIES", "")

    assert qbt_offload._offload_exclusions() == (["yaml-tag"], ["yaml-category"])


def test_offload_exclusions_default_to_empty_lists(monkeypatch) -> None:
    monkeypatch.setenv("WORKERS_CONFIG_PATH", "/missing/workers.yaml")
    monkeypatch.delenv("QBT_OFFLOAD_EXCLUDED_TAGS", raising=False)
    monkeypatch.delenv("QBT_OFFLOAD_EXCLUDED_CATEGORIES", raising=False)

    assert qbt_offload._offload_exclusions() == ([], [])


def test_catch_up_passes_exclusions_to_candidate_listing(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("WORKERS_CONFIG_PATH", "/missing/workers.yaml")
    monkeypatch.setenv("QBT_OFFLOAD_STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_TAGS", "private,keep")
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_CATEGORIES", "archive,manual")
    monkeypatch.setattr(qbt_offload, "QBittorrentClient", FakeQbt)

    def capture_candidates(_torrents, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(qbt_offload, "list_offload_candidates", capture_candidates)

    assert qbt_offload.run_catch_up(limit=20) == 0
    assert captured["excluded_tags"] == ["private", "keep"]
    assert captured["excluded_categories"] == ["archive", "manual"]


def test_hash_offload_passes_exclusions_to_candidate_planning(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("WORKERS_CONFIG_PATH", "/missing/workers.yaml")
    monkeypatch.setenv("QBT_OFFLOAD_STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_TAGS", "private")
    monkeypatch.setenv("QBT_OFFLOAD_EXCLUDED_CATEGORIES", "archive,manual")
    monkeypatch.setattr(qbt_offload, "QBittorrentClient", FakeQbt)

    def capture_candidate(_torrent, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(qbt_offload, "plan_offload_candidate", capture_candidate)

    assert qbt_offload.run_offload_hash("a" * 40) == 0
    assert captured["excluded_tags"] == ["private"]
    assert captured["excluded_categories"] == ["archive", "manual"]


@pytest.mark.parametrize(
    ("invalid_key", "invalid_value"),
    [
        ("excluded_tags", {"private": True}),
        ("excluded_categories", ["archive", 1]),
    ],
)
@pytest.mark.parametrize("runner", ["catch_up", "hash"])
def test_malformed_exclusions_stop_candidate_evaluation_and_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
    invalid_key: str,
    invalid_value: object,
    runner: str,
) -> None:
    config_path = tmp_path / "workers.yaml"
    status_path = tmp_path / "status.json"
    config_path.write_text(
        "offload:\n"
        f"  {invalid_key}: {invalid_value!r}\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    monkeypatch.setenv("WORKERS_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("QBT_OFFLOAD_STATUS_PATH", str(status_path))
    monkeypatch.delenv("QBT_OFFLOAD_EXCLUDED_TAGS", raising=False)
    monkeypatch.delenv("QBT_OFFLOAD_EXCLUDED_CATEGORIES", raising=False)
    monkeypatch.setattr(qbt_offload, "QBittorrentClient", FakeQbt)
    monkeypatch.setattr(
        qbt_offload,
        "list_offload_candidates",
        lambda *_args, **_kwargs: calls.append("candidate") or [],
    )
    monkeypatch.setattr(
        qbt_offload,
        "plan_offload_candidate",
        lambda *_args, **_kwargs: calls.append("candidate") or None,
    )
    monkeypatch.setattr(
        qbt_offload,
        "run_offload_moves",
        lambda *_args, **_kwargs: calls.append("mutation") or [],
    )

    result = (
        qbt_offload.run_catch_up()
        if runner == "catch_up"
        else qbt_offload.run_offload_hash("a" * 40)
    )

    assert result == 1
    assert invalid_key.upper() in capsys.readouterr().err
    assert calls == []
