from __future__ import annotations

from homelab_mcp.diagnostics.arr_queue import shape_queue_snapshot


def test_shape_queue_snapshot_counts_active() -> None:
    queue = {
        "totalRecords": 2,
        "records": [
            {
                "id": 1,
                "title": "Ep1",
                "status": "downloading",
                "size": 1000,
                "sizeleft": 500,
                "downloadId": "abc123",
                "trackedDownloadStatus": "ok",
            },
            {
                "id": 2,
                "title": "Ep2",
                "status": "completed",
                "size": 1000,
                "sizeleft": 0,
                "downloadId": "def456",
                "trackedDownloadStatus": "ok",
            },
        ],
    }
    data = shape_queue_snapshot(queue, service="sonarr", page_size=50)
    assert data["total"] == 2
    assert data["active_count"] == 1
    assert data["records"][0]["download_ids"] == ["abc123"]
    assert data["records"][0]["progress_pct"] == 50.0
