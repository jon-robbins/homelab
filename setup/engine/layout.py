"""Where local setup keeps its files, and the durable claim that this host belongs to it (0018, decision 2)."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

MODE = "local-setup"


class HostClaimed(RuntimeError):
    """The host belongs to the other setup mode (or to an unknown owner). Nothing was changed."""


class Layout:
    """Paths only. Building one writes nothing; `claim()` is the first write."""

    def __init__(self, root: str | Path = "/srv/homelab"):
        self.root = Path(root)
        self.setup_dir = self.root / "setup"
        self.marker = self.setup_dir / "owner"
        self.releases = self.setup_dir / "releases"

    def mode(self) -> str | None:
        try:
            return self.marker.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def refuse_owner_host(self) -> None:
        """The owner's signed-release pipeline leaves these; local setup must never share a host with it."""
        claimed = [
            path
            for path in (self.root / "trust" / "policy.json", self.root / "releases" / "current")
            if path.exists() or path.is_symlink()
        ]
        if claimed:
            raise HostClaimed(
                f"this host runs the signed-release pipeline ({claimed[0]} exists). Local setup does not adopt or "
                "overwrite it; use a host that was never set up that way."
            )

    def claim(self) -> None:
        self.refuse_owner_host()
        mode = self.mode()
        if mode not in (None, MODE):
            raise HostClaimed(
                f"{self.marker} says this host is owned by {mode!r}. If that marker is stale, check what is running, "
                "then delete it yourself and run setup again. Setup will not switch modes silently."
            )
        if mode is None:
            self.setup_dir.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".owner.", dir=self.setup_dir)
            with os.fdopen(fd, "w") as stream:
                stream.write(MODE + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.marker)
