from __future__ import annotations

from homelab_workers.arr_retry.health import (
    HealthActionState,
    HealthCandidate,
    HealthDecisionPlan,
    HealthPolicySettings,
    apply_action_budget,
    evaluate_candidate,
)


def _settings(**overrides: object) -> HealthPolicySettings:
    base = HealthPolicySettings(
        replace_age_seconds=12 * 3600,
        race_age_seconds=36 * 3600,
        min_average_speed_bps=10 * 1024,
        min_progress_to_skip_replace=0.95,
        cooldown_seconds=12 * 3600,
        max_replacements_per_day=2,
        max_actions_per_sweep=8,
        enable_health_replacement=True,
        enable_health_race=True,
    )
    values = {**base.__dict__, **overrides}
    return HealthPolicySettings(**values)


def _candidate(**overrides: object) -> HealthCandidate:
    base = HealthCandidate(
        app_label="sonarr",
        item_id=123,
        queue_id=456,
        title="Example",
        download_id="abc",
        age_seconds=13 * 3600,
        average_download_bps=5 * 1024,
        progress=0.40,
    )
    values = {**base.__dict__, **overrides}
    return HealthCandidate(**values)


def test_evaluate_candidate_replace_when_old_and_slow() -> None:
    decision = evaluate_candidate(_candidate(), _settings(), HealthActionState(), now_ts=1_700_000_000)
    assert decision.action == "replace"
    assert "below_speed_and_old" in decision.reason_codes


def test_evaluate_candidate_watch_when_too_new() -> None:
    decision = evaluate_candidate(
        _candidate(age_seconds=4 * 3600),
        _settings(),
        HealthActionState(),
        now_ts=1_700_000_000,
    )
    assert decision.action == "watch"
    assert "age_below_threshold" in decision.reason_codes


def test_evaluate_candidate_watch_when_high_progress() -> None:
    decision = evaluate_candidate(
        _candidate(progress=0.98),
        _settings(),
        HealthActionState(),
        now_ts=1_700_000_000,
    )
    assert decision.action == "watch"
    assert "high_progress" in decision.reason_codes


def test_evaluate_candidate_race_after_prior_replacement() -> None:
    prior = HealthActionState(replacement_timestamps=[1_699_950_000])
    decision = evaluate_candidate(
        _candidate(age_seconds=40 * 3600),
        _settings(),
        prior,
        now_ts=1_700_000_000,
    )
    assert decision.action == "race"
    assert "escalated_to_race" in decision.reason_codes


def test_evaluate_candidate_cooldown_blocks_action() -> None:
    prior = HealthActionState(replacement_timestamps=[1_699_999_000])
    decision = evaluate_candidate(
        _candidate(),
        _settings(),
        prior,
        now_ts=1_700_000_000,
    )
    assert decision.action == "watch"
    assert "cooldown_active" in decision.reason_codes


def test_evaluate_candidate_respects_daily_cap() -> None:
    prior = HealthActionState(replacement_timestamps=[1_699_940_000, 1_699_950_000])
    decision = evaluate_candidate(
        _candidate(),
        _settings(),
        prior,
        now_ts=1_700_000_000,
    )
    assert decision.action == "watch"
    assert "max_daily_replacements_reached" in decision.reason_codes


def test_apply_action_budget_limits_replace_and_race() -> None:
    plan = HealthDecisionPlan(
        decisions=[],
        replace_candidates=[_candidate(item_id=1), _candidate(item_id=2)],
        race_candidates=[_candidate(item_id=3), _candidate(item_id=4)],
    )
    clipped = apply_action_budget(plan, max_actions=3)
    assert len(clipped.replace_candidates) == 2
    assert len(clipped.race_candidates) == 1
