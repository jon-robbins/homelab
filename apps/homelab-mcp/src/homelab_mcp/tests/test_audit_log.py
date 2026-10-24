from __future__ import annotations

import json
from pathlib import Path

from homelab_mcp.audit_log import write_audit_record


def test_write_audit_record_appends_jsonl(tmp_path: Path, capsys) -> None:
    path = tmp_path / "audit.jsonl"
    write_audit_record(
        audit_path=str(path),
        tool="autobrr_irc_restart",
        tier=2,
        ok=True,
        duration_ms=10,
        detail={"network": "fictional-tracker"},
    )
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["tool"] == "autobrr_irc_restart"
    assert row["tier"] == 2
    assert row["ok"] is True
    assert "network" in row["detail"]
    assert "mutation" in capsys.readouterr().out
