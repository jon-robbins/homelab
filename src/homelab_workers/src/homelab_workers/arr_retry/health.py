from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from time import time


@dataclass(frozen=True)
class HealthPolicySettings:
    replace_age_seconds: int
    race_age_seconds: int
    min_average_speed_bps: float
    min_progress_to_skip_replace: float
    cooldown_seconds: int
    max_replacements_per_day: int
    max_actions_per_sweep: int
    enable_health_replacement: bool
    enable_health_race: bool


@dataclass(frozen=True)
class HealthCandidate:
    app_label: str
    item_id: int
    queue_id: int
    title: str
    download_id: str
    age_seconds: int
    average_download_bps: float
    progress: float


@dataclass(frozen=True)
class HealthDecision:
    action: str
    reason_codes: tuple[str, ...]
    candidate: HealthCandidate


@dataclass
class HealthActionState:
    replacement_timestamps: list[int] = field(default_factory=list)
    race_timestamps: list[int] = field(default_factory=list)

    def prune(self, now_ts: int) -> None:
        day_ago = now_ts - 86_400
        self.replacement_timestamps = [ts for ts in self.replacement_timestamps if ts >= day_ago]
        self.race_timestamps = [ts for ts in self.race_timestamps if ts >= day_ago]

    def last_action_ts(self) -> int | None:
        values = self.replacement_timestamps + self.race_timestamps
        return max(values) if values else None


@dataclass
class HealthStateStore:
    by_key: dict[str, HealthActionState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> HealthStateStore:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return cls()
        if not isinstance(raw, dict):
            return cls()
        by_key: dict[str, HealthActionState] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            replacements = value.get("replacement_timestamps") or []
            races = value.get("race_timestamps") or []
            by_key[key] = HealthActionState(
                replacement_timestamps=[int(v) for v in replacements if isinstance(v, (int, float))],
                race_timestamps=[int(v) for v in races if isinstance(v, (int, float))],
            )
        return cls(by_key=by_key)

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            key: {
                "replacement_timestamps": state.replacement_timestamps,
                "race_timestamps": state.race_timestamps,
            }
            for key, state in self.by_key.items()
        }
        p.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")

    def state_for(self, key: str) -> HealthActionState:
        state = self.by_key.get(key)
        if state is None:
            state = HealthActionState()
            self.by_key[key] = state
        return state


@dataclass
class HealthDecisionPlan:
    decisions: list[HealthDecision] = field(default_factory=list)
    replace_candidates: list[HealthCandidate] = field(default_factory=list)
    race_candidates: list[HealthCandidate] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        return len(self.replace_candidates) + len(self.race_candidates)


def now_unix_ts() -> int:
    return int(time())


def evaluate_candidate(
    candidate: HealthCandidate,
    settings: HealthPolicySettings,
    prior_state: HealthActionState,
    now_ts: int,
) -> HealthDecision:
    reasons: list[str] = []
    action = "healthy"

    if candidate.progress >= settings.min_progress_to_skip_replace:
        reasons.append("high_progress")
        return HealthDecision(action="watch", reason_codes=tuple(reasons), candidate=candidate)

    if candidate.age_seconds < settings.replace_age_seconds:
        reasons.append("age_below_threshold")
        return HealthDecision(action="watch", reason_codes=tuple(reasons), candidate=candidate)

    if candidate.average_download_bps >= settings.min_average_speed_bps:
        reasons.append("avg_speed_above_threshold")
        return HealthDecision(action="healthy", reason_codes=tuple(reasons), candidate=candidate)

    prior_state.prune(now_ts)
    last_action = prior_state.last_action_ts()
    if last_action is not None and now_ts - last_action < settings.cooldown_seconds:
        reasons.append("cooldown_active")
        return HealthDecision(action="watch", reason_codes=tuple(reasons), candidate=candidate)

    replacement_attempts = len(prior_state.replacement_timestamps)
    if replacement_attempts >= settings.max_replacements_per_day:
        reasons.append("max_daily_replacements_reached")
        return HealthDecision(action="watch", reason_codes=tuple(reasons), candidate=candidate)

    if (
        settings.enable_health_race
        and candidate.age_seconds >= settings.race_age_seconds
        and replacement_attempts > 0
    ):
        action = "race"
        reasons.append("escalated_to_race")
    elif settings.enable_health_replacement:
        action = "replace"
        reasons.append("below_speed_and_old")
    else:
        action = "watch"
        reasons.append("replacement_disabled")

    return HealthDecision(action=action, reason_codes=tuple(reasons), candidate=candidate)


def apply_action_budget(
    plan: HealthDecisionPlan,
    max_actions: int,
) -> HealthDecisionPlan:
    if plan.action_count <= max_actions:
        return plan
    keep_replace = max(0, max_actions)
    replace_candidates = plan.replace_candidates[:keep_replace]
    remaining = max_actions - len(replace_candidates)
    race_candidates = plan.race_candidates[: max(0, remaining)]
    return HealthDecisionPlan(
        decisions=plan.decisions,
        replace_candidates=replace_candidates,
        race_candidates=race_candidates,
    )
