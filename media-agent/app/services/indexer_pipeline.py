"""Direct Prowlarr indexer search + grab (no Sonarr/Radarr library required)."""
from __future__ import annotations

from typing import Any

import httpx

from ..config import Settings
from ..integrations.prowlarr import prowlarr_get, prowlarr_post_json
from .release_formatting import human_size, indexer_name, int_field


def _int(v: Any) -> int:
    return int_field(v) or 0


def run_indexer_search(
    client: httpx.Client,
    s: Settings,
    query: str,
    search_type: str,
    result_limit: int,
) -> dict:
    if not s.prowlarr_configured:
        return {
            "ok": False,
            "error": {
                "code": "PROWLARR_NOT_CONFIGURED",
                "message": (
                    "Set PROWLARR_URL and PROWLARR_API_KEY on media-agent to search "
                    "indexers directly."
                ),
            },
        }
    st = (search_type or "search").strip() or "search"
    q = " ".join(query.split())
    r = prowlarr_get(
        client,
        s,
        "api/v1/search",
        {"query": q, "type": st, "limit": min(result_limit, 100)},
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return {
            "ok": False,
            "error": {
                "code": "UPSTREAM_BAD_RESPONSE",
                "message": "prowlarr /search did not return a list",
            },
        }
    rows: list[dict[str, Any]] = [x for x in data if isinstance(x, dict)]
    rows.sort(
        key=lambda d: (
            -_int(d.get("seeders")),
            -_int(d.get("leechers")),
            -_int(d.get("size")),
        )
    )
    out: list[dict[str, Any]] = []
    for i, rel in enumerate(rows[:result_limit], start=1):
        sz = _int(rel.get("size"))
        out.append(
            {
                "rank": i,
                "title": str(rel.get("title") or "Unknown release"),
                "seeders": _int(rel.get("seeders")),
                "leechers": _int(rel.get("leechers")),
                "size": sz,
                "size_human": human_size(sz),
                "indexer": indexer_name(rel),
                "guid": str(rel.get("guid") or ""),
                "indexerId": _int(rel.get("indexerId")),
                "release": rel,
            }
        )
    return {
        "ok": True,
        "source": "prowlarr",
        "query": q,
        "search_type": st,
        "options": out,
    }


def prowlarr_grab(client: httpx.Client, s: Settings, release: dict) -> dict:
    if not s.prowlarr_configured:
        return {
            "ok": False,
            "error": {
                "code": "PROWLARR_NOT_CONFIGURED",
                "message": "Prowlarr is not configured for media-agent.",
            },
        }
    g = str(release.get("guid") or "").strip()
    iid = release.get("indexerId")
    if not g or iid is None or str(iid).strip() == "":
        return {
            "ok": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": (
                    "release must include non-empty guid and indexerId (use the "
                    "`release` object from indexer-search)."
                ),
            },
        }
    r = prowlarr_post_json(client, s, "api/v1/search", release)
    if r.status_code == 404:
        return {
            "ok": False,
            "error": {
                "code": "RELEASE_NOT_CACHED",
                "message": (
                    "Prowlarr no longer has this release in its grab cache; run "
                    "indexer-search again (cache ~30 minutes)."
                ),
            },
        }
    if r.status_code == 409 or r.status_code == 500:
        return {
            "ok": False,
            "error": {
                "code": "GRAB_FAILED",
                "message": f"prowlarr grab failed: HTTP {r.status_code}",
            },
        }
    if r.status_code >= 400:
        return {
            "ok": False,
            "error": {
                "code": "GRAB_FAILED",
                "message": f"prowlarr status {r.status_code}",
            },
        }
    try:
        body = r.json() if r.content else {}
    except Exception:  # noqa: BLE001
        body = {}
    return {"ok": True, "prowlarr": body, "source": "prowlarr"}


__all__ = ["run_indexer_search", "prowlarr_grab"]
