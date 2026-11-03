"""Where local setup keeps its files, and the durable claim that this host belongs to it (0018, decision 2)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

MODE = "local-setup"
OWNER_ROOT = Path("/srv/homelab")  # where the owner's signed-release pipeline lives, whatever --root says
UNIT_DIR = Path("/etc/systemd/system")
OWNER_PATHS = (
    "trust/policy.json",
    "releases/current",
    "tooling",
)  # install.sh leaves tooling before trust exists
OWNER_UNITS = tuple(f"homelab-{name}.{kind}" for name in ("controller", "edge-update") for kind in ("service", "timer"))


class SetupError(ValueError):
    """Something a person must fix before setup can continue; the message says what."""


class HostClaimed(SetupError):
    """The host belongs to the other setup mode (or to an unknown owner). Nothing was changed."""


class Layout:
    """Paths only. Building one writes nothing; `claim()` is the first write."""

    def __init__(
        self,
        root: str | Path = "/srv/homelab",
        owner_root: Path | None = None,
        unit_dir: Path | None = None,
    ):
        self.root = Path(root)
        self.owner_root = owner_root or OWNER_ROOT
        self.unit_dir = unit_dir or UNIT_DIR
        self.setup_dir = self.root / "setup"
        self.marker = self.root / "local-setup.owner"  # a flat file, so the owner's installer can look for it
        self.releases = self.setup_dir / "releases"

    def mode(self) -> str | None:
        try:
            return self.marker.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def refuse_owner_host(self) -> None:
        """The owner's signed-release pipeline leaves these; local setup must never share a host with it."""
        paths = [base / name for base in {self.root, self.owner_root} for name in OWNER_PATHS]
        paths += [self.unit_dir / name for name in OWNER_UNITS]
        claimed = [path for path in paths if path.exists() or path.is_symlink()]
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
        if self.root.resolve() != self.owner_root.resolve():
            Layout(self.owner_root, self.owner_root, self.unit_dir).claim()
        if mode is None:
            self.setup_dir.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".owner.", dir=self.setup_dir)
            with os.fdopen(fd, "w") as stream:
                stream.write(MODE + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.marker)
