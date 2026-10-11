from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_VALID_STATUSES = frozenset({"shipped", "deferred", "declined"})


def load_ledger(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        msg = f"ledger root must be a mapping: {path}"
        raise ValueError(msg)
    return data


def upsert_entry(
    ledger: dict[str, Any],
    *,
    entry_id: str,
    status: str,
    summary: str,
    updated_at: str,
) -> dict[str, Any]:
    if status not in _VALID_STATUSES:
        msg = f"invalid status {status!r}; expected one of {sorted(_VALID_STATUSES)}"
        raise ValueError(msg)

    entries = ledger.setdefault("entries", [])
    if not isinstance(entries, list):
        msg = "ledger entries must be a list"
        raise ValueError(msg)

    entry = {
        "id": entry_id,
        "status": status,
        "summary": summary,
        "updated_at": updated_at,
    }

    for idx, existing in enumerate(entries):
        if isinstance(existing, dict) and existing.get("id") == entry_id:
            entries[idx] = entry
            return ledger

    entries.append(entry)
    return ledger


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(ledger, f, sort_keys=False, allow_unicode=True)


def known_ids(ledger: dict[str, Any]) -> set[str]:
    entries = ledger.get("entries", [])
    if not isinstance(entries, list):
        return set()
    return {
        entry["id"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
