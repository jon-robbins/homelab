from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from homelab_mcp.tools.mutations import qbt


def _settings(mcp_settings):
    return replace(
        mcp_settings,
        media_hdd_mount="/mnt/media-hdd",
        media_nvme_mount="/mnt/media-nvme",
    )


@patch("homelab_mcp.tools.mutations.qbt.shutil.disk_usage")
@patch("homelab_mcp.tools.mutations.qbt.QBittorrentClient")
@patch("homelab_mcp.tools.mutations.qbt.worker_arr_status")
def test_plan_movie_is_read_only(mock_status, mock_qbt, mock_usage, mcp_settings):
    mock_status.return_value = {
        "ok": True,
        "library_id": 7,
        "title": "Example",
        "files": [{"path": "/mnt/media-hdd/Movies/Example.mkv", "size": 100}],
    }
    mock_usage.return_value = SimpleNamespace(free=1000)
    mock_qbt.return_value.__enter__.return_value.torrents_info_raw.return_value = [
        {"hash": "abc", "content_path": "/mnt/media-hdd/Movies", "size": 100, "progress": 1.0, "state": "seeding"}
    ]
    mock_qbt.return_value.__enter__.return_value.torrent_files.return_value = [{"name": "Example.mkv", "size": 100}]

    body = json.loads(qbt.media_title_to_nvme_plan(_settings(mcp_settings), media_type="movie", library_id=7))

    assert body["ok"] is True
    assert body["data"]["ready"] is True
    assert body["data"]["mutation"] is False
    assert body["data"]["destinations"] == ["/mnt/media-nvme/Movies/Example.mkv"]
    mock_qbt.return_value.__enter__.return_value.torrents_pause.assert_not_called()


@patch("homelab_mcp.tools.mutations.qbt.shutil.disk_usage")
@patch("homelab_mcp.tools.mutations.qbt.QBittorrentClient")
@patch("homelab_mcp.tools.mutations.qbt.worker_arr_status")
def test_plan_series_defaults_to_one_season_and_blocks_missing_owner(mock_status, mock_qbt, mock_usage, mcp_settings):
    mock_status.return_value = {
        "ok": True,
        "library_id": 9,
        "title": "Series",
        "files": [
            {"path": "/mnt/media-hdd/TV/Series/S01E01.mkv", "season": 1, "episode": 1, "size": 10},
            {"path": "/mnt/media-hdd/TV/Series/S02E01.mkv", "season": 2, "episode": 1, "size": 10},
        ],
    }
    mock_usage.return_value = SimpleNamespace(free=1000)
    mock_qbt.return_value.__enter__.return_value.torrents_info_raw.return_value = []

    body = json.loads(qbt.media_title_to_nvme_plan(_settings(mcp_settings), media_type="series", library_id=9))

    assert body["ok"] is True
    assert body["data"]["selection"] == "s1"
    assert body["data"]["ready"] is False
    assert "qbt_ownership_missing" in body["data"]["blockers"]
