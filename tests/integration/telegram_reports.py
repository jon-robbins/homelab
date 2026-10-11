"""Deterministic, secret-safe Telegram test report serialization."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from telegram_cases import SECRET_KEYS, TestRunResult

SECRET_VALUE = re.compile(r"(?i)(bearer\s+|(?:token|secret|password|api[_-]?(?:key|hash))\s*[=:]\s*)[^\s,;]+")
TOKEN_SHAPED_VALUE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{16,}\b")


def redact(value: Any, configured_values: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET_KEYS.search(str(key)) else redact(item, configured_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, configured_values) for item in value]
    if isinstance(value, tuple):
        return [redact(item, configured_values) for item in value]
    if isinstance(value, str):
        for secret in sorted({item for item in configured_values if item}, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = SECRET_VALUE.sub(lambda match: match.group(1) + "[REDACTED]", value)
        return TOKEN_SHAPED_VALUE.sub("[REDACTED_TOKEN]", value)
    return value


def serialize_report(result: TestRunResult, configured_values: tuple[str, ...] = ()) -> str:
    payload = asdict(result)
    cases = payload["cases"]
    payload["summary"] = {
        "total": len(cases),
        "passed": sum(1 for case in cases if case["passed"]),
        "failed": sum(1 for case in cases if not case["passed"]),
    }
    return json.dumps(redact(payload, configured_values), indent=2, sort_keys=True) + "\n"


def write_report(result: TestRunResult, output: Path, configured_values: tuple[str, ...] = ()) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output.with_suffix(f"{output.suffix}.tmp")
    temp_path.write_text(serialize_report(result, configured_values), encoding="utf-8")
    temp_path.replace(output)


def append_jsonl_record(record: Any, path: Path, configured_values: tuple[str, ...] = ()) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(record, "__dataclass_fields__"):
        data = asdict(record)
    elif isinstance(record, dict):
        data = record
    else:
        data = {"value": str(record)}
    redacted = redact(data, configured_values)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(redacted, separators=(",", ":")) + "\n")
