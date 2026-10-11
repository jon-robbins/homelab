from __future__ import annotations

from typing import Any

from homelab_workers.shared.prowlarr import ProwlarrClient
from homelab_workers.shared.release_formatting import human_size, indexer_name, int_field, release_flag_fields


def search_prowlarr_releases(
    client: ProwlarrClient,
    *,
    query: str,
    search_type: str = "search",
    limit: int = 10,
    indexer_ids: list[int] | None = None,
) -> dict[str, Any]:
    rows = client.search(
        query=query,
        search_type=search_type,
        limit=min(limit, 25),
        indexer_ids=indexer_ids,
    )
    rows.sort(
        key=lambda row: (
            -(int_field(row.get("seeders")) or 0),
            -(int_field(row.get("leechers")) or 0),
            -(int_field(row.get("size")) or 0),
        )
    )
    options: list[dict[str, Any]] = []
    normalized_query = " ".join(query.split())
    for rank, rel in enumerate(rows[: min(limit, 25)], start=1):
        size = int_field(rel.get("size")) or 0
        options.append(
            {
                "rank": rank,
                "title": str(rel.get("title") or "Unknown release"),
                "seeders": int_field(rel.get("seeders")) or 0,
                "leechers": int_field(rel.get("leechers")) or 0,
                "size_bytes": size,
                "size_human": human_size(size),
                "indexer": indexer_name(rel),
                "guid": str(rel.get("guid") or ""),
                "indexer_id": int_field(rel.get("indexerId")) or 0,
                **release_flag_fields(rel),
            }
        )
    return {
        "ok": True,
        "source": "prowlarr",
        "query": normalized_query,
        "search_type": (search_type or "search").strip() or "search",
        "options": options,
    }


def grab_prowlarr_release(
    client: ProwlarrClient,
    *,
    guid: str,
    indexer_id: int,
    title: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"guid": guid.strip(), "indexerId": indexer_id}
    if title:
        body["title"] = title
    response = client.grab_release(body)
    if response.status_code == 404:
        return {
            "ok": False,
            "error": {
                "code": "RELEASE_NOT_CACHED",
                "message": "Prowlarr no longer has this release cached; run prowlarr_indexer_search again.",
            },
        }
    if response.status_code in {409, 500} or response.status_code >= 400:
        return {
            "ok": False,
            "error": {
                "code": "GRAB_FAILED",
                "message": f"prowlarr grab failed: HTTP {response.status_code}",
            },
        }
    try:
        payload = response.json() if response.content else {}
    except Exception:  # noqa: BLE001
        payload = {}
    return {"ok": True, "source": "prowlarr", "prowlarr": payload}
