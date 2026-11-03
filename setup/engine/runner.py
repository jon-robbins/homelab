"""Run steps in dependency order, resuming from whatever `check()` says is already true."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .checkpoints import AlreadyRunning
from .contract import Context, Result, State, Status, Step


@dataclass(frozen=True)
class Event:
    step: str
    kind: str  # skipped | applying | done | blocked | failed
    message: str = ""


@dataclass(frozen=True)
class Outcome:
    ok: bool
    done: tuple[str, ...] = ()
    stopped_at: str | None = None
    reason: str = ""


def order(steps: Iterable[Step]) -> list[Step]:
    """Dependency order, ties broken by list order. Refuses duplicates, unknown needs and cycles."""
    steps = list(steps)
    ids = [step.id for step in steps]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate step id: {sorted({i for i in ids if ids.count(i) > 1})}")
    for step in steps:
        for need in step.needs:
            if need not in ids:
                raise ValueError(f"step {step.id} needs unknown step {need}")
    placed: list[Step] = []
    while len(placed) < len(steps):
        done = {step.id for step in placed}
        ready = [s for s in steps if s.id not in done and all(n in done for n in s.needs)]
        if not ready:
            raise ValueError(f"cycle among steps: {sorted(set(ids) - done)}")
        placed.append(ready[0])
    return placed


def status(steps: Sequence[Step], ctx: Context) -> dict[str, State]:
    """Read-only: every step's check(), in order. Writes nothing."""
    return {step.id: step.check(ctx) for step in order(steps)}


def _safe(ctx: Context, value):
    if isinstance(value, str):
        return ctx.secrets.redact(value)
    if isinstance(value, dict):
        return {_safe(ctx, key): _safe(ctx, item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(ctx, item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise TypeError("facts must contain JSON data, never secret handles")


def _publish(ctx: Context, result: Result) -> None:
    facts = _safe(ctx, dict(result.facts))
    for key, value in facts.items():
        ctx.facts[key] = value
        ctx.checkpoints.set_fact(key, value)


def _converge(step: Step, ctx: Context) -> Result:
    """Make one step true. Returns the failure, or success once check/verify agree it is done."""
    state = step.check(ctx)
    if state.status is Status.BLOCKED:
        return Result.failure(state.reason)
    if state.status is Status.DONE:
        verified = step.verify(ctx)
        if verified.ok:
            return verified
    ctx.checkpoints.begin(step.id)
    applied = step.apply(ctx)
    if not applied.ok:
        return applied
    _publish(ctx, applied)
    return step.verify(ctx)


def run(
    steps: Sequence[Step],
    ctx: Context,
    *,
    on_event: Callable[[Event], None] = lambda event: None,
) -> Outcome:
    planned = order(steps)
    try:
        with ctx.checkpoints.lock():
            return _run(planned, ctx, on_event)
    except AlreadyRunning as error:
        return Outcome(False, reason=str(error))


def _run(planned: list[Step], ctx: Context, on_event: Callable[[Event], None]) -> Outcome:
    ctx.facts.update(ctx.checkpoints.facts())
    finished: list[str] = []
    for step in planned:
        on_event(Event(step.id, "applying"))
        try:
            result = _converge(step, ctx)
            if result.ok:
                _publish(ctx, result)
        except Exception as error:  # a crash in one step is that step's failure, with the cause kept
            result = Result.failure(f"{type(error).__name__}: {error}")
        message = ctx.secrets.redact(result.message)
        if not result.ok:
            ctx.checkpoints.failed(step.id, message)
            on_event(Event(step.id, "failed", message))
            return Outcome(False, tuple(finished), step.id, message)
        ctx.checkpoints.done(step.id, message)
        on_event(Event(step.id, "done", message))
        finished.append(step.id)
    return Outcome(True, tuple(finished))
