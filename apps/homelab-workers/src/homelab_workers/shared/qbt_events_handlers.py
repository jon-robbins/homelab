from __future__ import annotations

from typing import Any, TypedDict

from homelab_workers.shared import qbt_exclusive as ex

EXCLUSIVE_HANDLER_NAME = "exclusive"


class HandlerAck(TypedDict):
    name: str
    handled: bool
    action: str
    reason: str
    detail: dict[str, Any]


def _exclusive_handler(qbt: Any, *, hash_value: str, session_path: str) -> HandlerAck:
    result = ex.mark_finished(qbt, hash_value=hash_value, session_path=session_path)
    return HandlerAck(
        name=EXCLUSIVE_HANDLER_NAME,
        handled=result["handled"],
        action=result["action"],
        reason=result["reason"],
        detail=result["detail"],
    )


def dispatch_torrent_finished(qbt: Any, *, hash_value: str, session_path: str) -> dict[str, Any]:
    handlers: list[HandlerAck] = [
        _exclusive_handler(qbt, hash_value=hash_value, session_path=session_path),
    ]
    return {
        "ok": True,
        "hash": hash_value,
        "handlers": handlers,
    }
