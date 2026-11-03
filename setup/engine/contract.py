"""The step contract every setup package implements (see setup/README.md). Pure data and a Protocol: no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

from .checkpoints import Checkpoints
from .layout import Layout
from .secrets import SecretStore


class Status(str, Enum):
    DONE = "done"
    NEEDED = "needed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class State:
    status: Status
    reason: str = ""

    @classmethod
    def done(cls) -> State:
        return cls(Status.DONE)

    @classmethod
    def needed(cls) -> State:
        return cls(Status.NEEDED)

    @classmethod
    def blocked(cls, reason: str) -> State:
        return cls(Status.BLOCKED, reason)


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str = ""
    facts: Mapping[str, Any] = field(default_factory=dict)  # non-secret outputs later steps need (IPs, IDs)

    @classmethod
    def success(cls, message: str = "", facts: Mapping[str, Any] | None = None) -> Result:
        return cls(True, message, dict(facts or {}))

    @classmethod
    def failure(cls, message: str) -> Result:
        return cls(False, message)


@dataclass
class Context:
    layout: Layout
    secrets: SecretStore
    checkpoints: Checkpoints
    platform: Path  # the homelab checkout or verified release the renderer reads
    site: Path  # the person's local site directory
    facts: dict[str, Any] = field(default_factory=dict)


class Step(Protocol):
    id: str  # register ID, e.g. "CF-6"
    needs: tuple[str, ...]  # step IDs that must be done first

    def check(self, ctx: Context) -> State:
        """Read-only. DONE, NEEDED, or BLOCKED with a reason a person can act on."""

    def apply(self, ctx: Context) -> Result:
        """Safe to re-run."""

    def verify(self, ctx: Context) -> Result:
        """Prove it worked, from outside the step."""


class CheckIsProof:
    """Mixin for a step whose check() already observes the host: verify() is check() reporting DONE."""

    def verify(self, ctx: Context) -> Result:
        state = self.check(ctx)  # type: ignore[attr-defined]
        if state.status is Status.DONE:
            return Result.success()
        return Result.failure(state.reason or f"{self.id} is still not done after apply")  # type: ignore[attr-defined]
