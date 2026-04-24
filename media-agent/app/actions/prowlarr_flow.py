"""
Direct Prowlarr indexer search + grab (no Sonarr/Radarr library required).

- GET  /api/v1/search?query=...&type=...&limit=...  (see Prowlarr SearchResource)
- POST /api/v1/search  (body = ReleaseResource from search; uses Prowlarr 30m grab cache)
"""

from __future__ import annotations

from typing import Any, List
from urllib.parse import urljoin

import httpx

from ..core.config import Settings
from .download_options import human_size


def _pl_headers(s: Settings) -> dict:
    return {"X-Api-Key": s.prowlarr_api_key}


def _indexer_name(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("id") or "unknown")
    return "unknown"


def _int(v: Any) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


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
                "message": "Set PROWLARR_URL and PROWLARR_API_KEY on media-agent to search indexers directly.",
            },
        }
    st = (search_type or "search").strip() or "search"
    q = " ".join(query.split())
    url = urljoin(f"{s.prowlarr_base}/", "api/v1/search")
    r = client.get(
        url,
        params={"query": q, "type": st, "limit": min(result_limit, 100)},
        headers=_pl_headers(s),
        timeout=s.prowlarr_search_timeout_s,
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
    rows: List[dict[str, Any]] = [x for x in data if isinstance(x, dict)]
    # Prefer higher seeders; Prowlarr may already sort — keep a stable top-N
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
                "indexer": _indexer_name(rel.get("indexer")),
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
                "message": "release must include non-empty guid and indexerId (use the `release` object from indexer-search).",
            },
        }
    url = urljoin(f"{s.prowlarr_base}/", "api/v1/search")
    r = client.post(
        url,
        json=release,
        headers={**_pl_headers(s), "Content-Type": "application/json"},
        timeout=s.prowlarr_search_timeout_s,
    )
    if r.status_code == 404:
        return {
            "ok": False,
            "error": {
                "code": "RELEASE_NOT_CACHED",
                "message": "Prowlarr no longer has this release in its grab cache; run indexer-search again (cache ~30 minutes).",
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
