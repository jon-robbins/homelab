from __future__ import annotations

from typing import Any

from homelab_workers.shared.arr import (
    _ACTIVE_ARR_QUEUE_STATUSES,
    _arr_queue_record_active,
    _queue_record_download_ids,
)


def _queue_row(rec: dict[str, Any]) -> dict[str, Any]:
    size = int(rec.get("size") or 0)
    sizeleft = int(rec.get("sizeleft") or 0)
    progress_pct = None
    if size > 0:
        progress_pct = round(max(0.0, min(100.0, 100.0 * (size - sizeleft) / size)), 1)
    return {
        "id": rec.get("id"),
        "title": rec.get("title"),
        "status": rec.get("status"),
        "tracked_download_status": rec.get("trackedDownloadStatus"),
        "download_ids": _queue_record_download_ids(rec),
        "size_bytes": size,
        "sizeleft_bytes": sizeleft,
        "progress_pct": progress_pct,
        "active": _arr_queue_record_active(rec),
    }


def shape_queue_snapshot(queue: dict[str, Any], *, service: str, page_size: int) -> dict[str, Any]:
    records = [_queue_row(rec) for rec in queue.get("records") or [] if isinstance(rec, dict)]
    by_status: dict[str, int] = {}
    for rec in records:
        status = str(rec.get("status") or "unknown").lower()
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "service": service,
        "total": int(queue.get("totalRecords") or len(records)),
        "page_size": page_size,
        "active_count": sum(1 for rec in records if rec.get("active")),
        "by_status": by_status,
        "active_statuses": sorted(_ACTIVE_ARR_QUEUE_STATUSES),
        "records": records,
    }
