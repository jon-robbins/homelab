from __future__ import annotations

from pathlib import Path


PUBLIC_ROOT = Path(__file__).parents[2]
PUBLIC_SOURCE = PUBLIC_ROOT / "homelab_workers"
PUBLIC_CLI = PUBLIC_SOURCE / "cli"


def test_public_source_has_no_private_tracker_modules() -> None:
    private_cli = sorted(
        path.name
        for pattern in ("tl_*.py", "ratio_farm_*.py")
        for path in PUBLIC_CLI.glob(pattern)
    )
    private_shared = sorted(
        path.name
        for path in PUBLIC_SOURCE.joinpath("shared").glob("tl_*.py")
    )
    assert private_cli == []
    assert private_shared == []
    assert not (PUBLIC_SOURCE / "shared" / "ratiofarm_orphans.py").exists()
    assert not (PUBLIC_SOURCE / "shared" / "plex_ratio_ready.py").exists()


def test_public_source_has_no_reverse_tracker_dependency() -> None:
    offenders: list[str] = []
    for path in PUBLIC_SOURCE.rglob("*.py"):
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8")
        if any(
            line.startswith(("from tracker_integrations", "import tracker_integrations"))
            for line in text.splitlines()
        ):
            offenders.append(str(path.relative_to(PUBLIC_ROOT)))
    assert offenders == []
