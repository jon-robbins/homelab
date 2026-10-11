"""Blast-radius controls for destructive media-agent operations.

Grab actions and unsafe HTTP verbs require explicit clearance via
``MEDIA_AGENT_ALLOW_DESTRUCTIVE`` or the ``X-Media-Agent-Destructive-Confirm``
header matching ``MEDIA_AGENT_DESTRUCTIVE_CONFIRM_TOKEN``.
"""

from __future__ import annotations

from secrets import compare_digest
from typing import TYPE_CHECKING

from .auth import err_response

if TYPE_CHECKING:
    from ..config import Settings

DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset(
    {
        "download_grab_tv",
        "download_grab_movie",
        "indexer_grab",
    }
)

DESTRUCTIVE_POST_PATH_SUFFIXES: frozenset[str] = frozenset(
    {
        "/download-grab",
        "/indexer-grab",
    }
)

BLOCKED_HTTP_METHODS: frozenset[str] = frozenset({"DELETE", "PUT"})


def is_destructive_action(action: str | None) -> bool:
    return bool(action) and action in DESTRUCTIVE_ACTIONS


def destructive_action_from_payload(payload: dict) -> str | None:
    action = str((payload or {}).get("action") or "").strip()
    return action if is_destructive_action(action) else None


def destructive_clearance_granted(
    settings: Settings,
    *,
    destructive_confirm: str | None,
) -> bool:
    if settings.media_agent_allow_destructive:
        return True
    expected = (settings.media_agent_destructive_confirm_token or "").strip()
    if not expected:
        return False
    got = (destructive_confirm or "").strip()
    return bool(got) and compare_digest(got, expected)


def destructive_blocked_response(
    request_id: str,
    *,
    detail: str = "destructive action blocked; set MEDIA_AGENT_ALLOW_DESTRUCTIVE or send X-Media-Agent-Destructive-Confirm",
) -> object:
    return err_response(request_id, "FORBIDDEN", detail, 403)


def assert_destructive_allowed(
    settings: Settings,
    request_id: str,
    *,
    action: str | None = None,
    destructive_confirm: str | None = None,
) -> object | None:
    if not is_destructive_action(action):
        return None
    if destructive_clearance_granted(
        settings, destructive_confirm=destructive_confirm
    ):
        return None
    return destructive_blocked_response(request_id)


def assert_http_method_allowed(
    settings: Settings,
    request_id: str,
    method: str,
    *,
    destructive_confirm: str | None = None,
) -> object | None:
    if method.upper() not in BLOCKED_HTTP_METHODS:
        return None
    if destructive_clearance_granted(
        settings, destructive_confirm=destructive_confirm
    ):
        return None
    return destructive_blocked_response(
        request_id,
        detail=f"{method.upper()} is not permitted without destructive clearance",
    )


def assert_destructive_post_path_allowed(
    settings: Settings,
    request_id: str,
    path: str,
    *,
    destructive_confirm: str | None = None,
) -> object | None:
    normalized = path.rstrip("/") or "/"
    if not any(normalized.endswith(suffix) for suffix in DESTRUCTIVE_POST_PATH_SUFFIXES):
        return None
    if destructive_clearance_granted(
        settings, destructive_confirm=destructive_confirm
    ):
        return None
    return destructive_blocked_response(request_id)
