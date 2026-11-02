"""Run steps in dependency order, resuming from whatever `check()` says is already true."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .checkpoints import AlreadyRunning
from .secrets import SecretHandle
from .contract import Answer, Context, Prompt, Result, Secret, State, Status, Step

Ask = Callable[[Prompt], Answer]
MAX_PROMPTS = 8  # per step; a step that keeps asking is broken


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


def _store(ctx: Context, prompt: Prompt, answer: Answer) -> None:
    if isinstance(prompt, Secret):
        ctx.answers[prompt.key] = (
            answer if isinstance(answer, SecretHandle) else ctx.secrets.put(prompt.key, str(answer))
        )  # the value goes to a file, only a handle stays
    else:
        if isinstance(answer, SecretHandle):
            raise TypeError("secret handles require a Secret prompt")
        ctx.answers[prompt.key] = answer
        ctx.checkpoints.set_answer(prompt.key, answer)


def _apply(step: Step, ctx: Context, ask: Ask | None) -> Result:
    result = step.apply(ctx)
    for _ in range(MAX_PROMPTS):
        if result.prompt is None:
            return result
        if ask is None:
            return Result.failure(f"needs your input: {result.prompt.label}")
        _store(ctx, result.prompt, ask(result.prompt))
        result = step.apply(ctx)
    return Result.failure(f"{step.id} kept asking for input")


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


def _converge(step: Step, ctx: Context, ask: Ask | None) -> Result:
    """Make one step true. Returns the failure, or success once check/verify agree it is done."""
    state = step.check(ctx)
    if state.status is Status.BLOCKED:
        return Result.failure(state.reason)
    if state.status is Status.DONE:
        verified = step.verify(ctx)
        if verified.ok:
            return verified
    ctx.checkpoints.begin(step.id)
    applied = _apply(step, ctx, ask)
    if not applied.ok:
        return applied
    _publish(ctx, applied)
    return step.verify(ctx)


def run(
    steps: Sequence[Step],
    ctx: Context,
    *,
    ask: Ask | None = None,
    on_event: Callable[[Event], None] = lambda event: None,
) -> Outcome:
    planned = order(steps)
    try:
        with ctx.checkpoints.lock():
            return _run(planned, ctx, ask, on_event)
    except AlreadyRunning as error:
        return Outcome(False, reason=str(error))


def _run(planned: list[Step], ctx: Context, ask: Ask | None, on_event: Callable[[Event], None]) -> Outcome:
    ctx.facts.update(ctx.checkpoints.facts())
    ctx.answers.update(ctx.checkpoints.answers())
    finished: list[str] = []
    for step in planned:
        on_event(Event(step.id, "applying"))
        try:
            result = _converge(step, ctx, ask)
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
