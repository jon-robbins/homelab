from homelab_workers.shared.seerr_reconciler_state import (
    build_reconciler_status,
    evaluate_reconciler_health,
    failed_guids_for,
    read_reconciler_state,
    record_attempt,
    write_reconciler_state,
)


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    write_reconciler_state(str(path), {"requests": {"r1": {"state": "available"}}})

    assert read_reconciler_state(str(path)) == {"requests": {"r1": {"state": "available"}}}


def test_health_disabled_is_healthy():
    status = build_reconciler_status(
        enabled=False,
        fallback_enabled=False,
        running=False,
        ok=True,
        dry_run=False,
        started_at=100,
        completed_at=100,
        diagnosed=0,
        mutated=0,
        blocked=0,
    )

    assert evaluate_reconciler_health(status, 900, now=200)["healthy"] is True


def test_health_stale_enabled_status_is_unhealthy():
    status = build_reconciler_status(
        enabled=True,
        fallback_enabled=False,
        running=False,
        ok=True,
        dry_run=True,
        started_at=100,
        completed_at=100,
        diagnosed=3,
        mutated=0,
        blocked=1,
    )

    result = evaluate_reconciler_health(status, 50, now=200)

    assert result["healthy"] is False
    assert result["error_code"] == "STALE_STATUS"


def test_health_uses_explicit_zero_now():
    status = build_reconciler_status(
        enabled=True,
        fallback_enabled=False,
        running=False,
        ok=True,
        dry_run=True,
        started_at=0,
        completed_at=0,
        diagnosed=0,
        mutated=0,
        blocked=0,
    )

    result = evaluate_reconciler_health(status, 0, now=0)

    assert result["healthy"] is True
    assert result["age_seconds"] == 0


def test_record_attempt_tracks_failed_guid_once():
    state = record_attempt({}, "req:1", "guid-1", ok=False, now=123)
    state = record_attempt(state, "req:1", "guid-1", ok=False, now=124)

    assert failed_guids_for(state, "req:1") == {"guid-1"}
