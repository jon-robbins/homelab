from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from homelab_mcp.tools.mutations import qbt


def test_apply_profile_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        qbt.apply_profile(
            settings,
            profile="max-downloads",
            expected_action="wrong",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"


@patch("homelab_mcp.tools.mutations.qbt.QBittorrentClient")
def test_apply_profile_applies(mock_cls, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        qbt.apply_profile(
            settings,
            profile="max-downloads",
            expected_action="profile:max-downloads",
        )
    )
    assert body["ok"] is True
    mock_cls.return_value.__enter__.return_value.set_app_preferences.assert_called_once()


def test_torrents_pause_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        qbt.torrents_pause(settings, tag="freeleech", expected_action="pause:tag:seed-group")
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"


@patch("homelab_mcp.tools.mutations.qbt.QBittorrentClient")
def test_torrents_pause_applies(mock_cls, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_cls.return_value.__enter__.return_value.torrents_pause.return_value = 2
    body = json.loads(
        qbt.torrents_pause(
            settings,
            tag="freeleech",
            expected_action="pause:tag:freeleech",
        )
    )
    assert body["ok"] is True
    assert body["data"]["applied"]["paused_count"] == 2


def test_offload_run_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(qbt.offload_run(settings, limit=2, expected_action="offload:1"))
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"


@patch("homelab_mcp.tools.mutations.qbt.run_offload_moves")
@patch("homelab_mcp.tools.mutations.qbt.list_offload_candidates")
@patch("homelab_mcp.tools.mutations.qbt.QBittorrentClient")
def test_offload_run_applies(mock_cls, mock_list, mock_run, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_list.return_value = [{"hash": "a" * 40}]
    mock_run.return_value = [{"ok": True, "hash": "a" * 40}]
    body = json.loads(
        qbt.offload_run(settings, limit=1, expected_action="offload:1")
    )
    assert body["ok"] is True
    assert body["data"]["applied"]["moved_count"] == 1
    mock_list.assert_called_once_with(
        mock_cls.return_value.__enter__.return_value.torrents_info_raw.return_value,
        nvme_mount=settings.media_nvme_mount,
        hdd_mount=settings.media_hdd_mount,
        limit=1,
    )
    mock_run.assert_called_once_with(
        mock_cls.return_value.__enter__.return_value,
        mock_list.return_value,
        dry_run=False,
        min_hdd_free_bytes=settings.qbt_offload_min_hdd_free_bytes,
    )
