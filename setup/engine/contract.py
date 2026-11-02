"""The step contract every setup package implements (see setup/README.md). Pure data and a Protocol: no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .checkpoints import Checkpoints
from .layout import Layout
from .secrets import SecretHandle, SecretStore


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
class Prompt:
    key: str  # answers are filed under this; a Secret's key is also its file name
    label: str


@dataclass(frozen=True)
class Text(Prompt):
    default: str | None = None


@dataclass(frozen=True)
class Secret(Prompt):
    hint: str = ""  # where the person gets it


@dataclass(frozen=True)
class Confirm(Prompt):
    pass


@dataclass(frozen=True)
class Choice(Prompt):
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenUrl(Prompt):
    url: str = ""  # answered True once the person has done what the page asks


Answer = str | bool | SecretHandle


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str = ""
    prompt: Prompt | None = None
    facts: Mapping[str, Any] = field(default_factory=dict)  # non-secret outputs later steps need (IPs, IDs)

    @classmethod
    def success(cls, message: str = "", facts: Mapping[str, Any] | None = None) -> Result:
        return cls(True, message, None, dict(facts or {}))

    @classmethod
    def failure(cls, message: str) -> Result:
        return cls(False, message)

    @classmethod
    def ask(cls, prompt: Prompt) -> Result:
        return cls(False, prompt.label, prompt)


@dataclass
class Context:
    layout: Layout
    secrets: SecretStore
    checkpoints: Checkpoints
    platform: Path  # the homelab checkout or verified release the renderer reads
    site: Path  # the person's local site directory
    plugins: Mapping[str, Path] = field(default_factory=dict)
    answers: dict[str, Answer] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Step(Protocol):
    id: str  # register ID, e.g. "CF-6"
    needs: tuple[str, ...]  # step IDs that must be done first

    def check(self, ctx: Context) -> State:
        """Read-only. DONE, NEEDED, or BLOCKED with a reason a person can act on."""

    def apply(self, ctx: Context) -> Result:
        """Safe to re-run. No screen code: ask for input by returning `Result.ask(prompt)`."""

    def verify(self, ctx: Context) -> Result:
        """Prove it worked, from outside the step."""
