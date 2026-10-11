from homelab_workers.shared import qbt_offload_state
from homelab_workers.shared.qbt_offload_state import (
    evaluate_qbt_offload_health,
    read_qbt_offload_status,
    write_qbt_offload_status,
)


def test_health_unhealthy_when_hdd_full() -> None:
    status = {"updated_at": 100, "hdd_full": True, "pending_on_nvme": 2}

    health = evaluate_qbt_offload_health(status, max_age_seconds=7200, now=100)

    assert health["healthy"] is False
    assert health["reason"] == "hdd_full"


def test_health_unhealthy_when_status_is_stale() -> None:
    status = {"updated_at": 100, "hdd_full": False, "pending_on_nvme": 0, "failed": 0}

    health = evaluate_qbt_offload_health(status, max_age_seconds=10, now=111)

    assert health == {"healthy": False, "reason": "stale_heartbeat", "age_seconds": 11}


def test_status_round_trip(tmp_path) -> None:
    path = tmp_path / "state" / "status.json"
    status = {
        "updated_at": 100,
        "last_run": 99,
        "offloaded": 2,
        "failed": 0,
        "hdd_full": False,
        "pending_on_nvme": 3,
        "ignored": "not persisted",
    }

    write_qbt_offload_status(path, status)

    assert read_qbt_offload_status(path) == {
        "updated_at": 100,
        "last_run": 99,
        "offloaded": 2,
        "failed": 0,
        "hdd_full": False,
        "pending_on_nvme": 3,
    }


def test_status_writes_use_unique_temporary_files(tmp_path, monkeypatch) -> None:
    path = tmp_path / "status.json"
    temporary_paths = []
    original_replace = qbt_offload_state.os.replace

    def record_replace(source, target) -> None:
        temporary_paths.append(source)
        original_replace(source, target)

    monkeypatch.setattr(qbt_offload_state.os, "replace", record_replace)

    write_qbt_offload_status(path, {"updated_at": 100})
    write_qbt_offload_status(path, {"updated_at": 101})

    assert temporary_paths[0] != temporary_paths[1]
