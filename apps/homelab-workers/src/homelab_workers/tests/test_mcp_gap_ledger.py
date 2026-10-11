from pathlib import Path

from homelab_workers.mcp_gap_closer.ledger import (
    known_ids,
    load_ledger,
    save_ledger,
    upsert_entry,
)


def test_upsert_and_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "ledger.yml"
    path.write_text("version: 1\nentries: []\n", encoding="utf-8")
    ledger = load_ledger(path)
    ledger = upsert_entry(
        ledger,
        entry_id="gap-a",
        status="shipped",
        summary="tool a",
        updated_at="2026-08-10T06:00:00Z",
    )
    save_ledger(path, ledger)
    loaded = load_ledger(path)
    assert "gap-a" in known_ids(loaded)
