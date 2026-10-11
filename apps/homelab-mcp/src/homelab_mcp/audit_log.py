from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_audit_record(
    *,
    audit_path: str,
    tool: str,
    tier: int,
    ok: bool,
    duration_ms: int,
    detail: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    row = {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tool": tool,
        "tier": tier,
        "mutation": True,
        "ok": ok,
        "duration_ms": duration_ms,
        "detail": detail or {},
    }
    if error_code:
        row["error_code"] = error_code
    line = json.dumps(row, separators=(",", ":"))
    print(f"AUDIT {line}", file=sys.stdout, flush=True)
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
