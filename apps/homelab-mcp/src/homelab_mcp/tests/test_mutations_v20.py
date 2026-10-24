from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from homelab_mcp.tools.mutations import qbt


@patch("homelab_mcp.tools.mutations.qbt.QBittorrentClient")
def test_torrents_resume_requires_confirm(mock_cls, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        qbt.torrents_resume(settings, tag="freeleech", confirm=False)
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "CONFIRMATION_REQUIRED"
    mock_cls.assert_not_called()


@patch("homelab_mcp.tools.mutations.qbt.QBittorrentClient")
def test_offload_candidates_read_only(mock_cls, mcp_settings) -> None:
    mock_client = mock_cls.return_value.__enter__.return_value
    mock_client.torrents_info_raw.return_value = []
    body = json.loads(qbt.offload_candidates(mcp_settings, limit=5))
    assert body["ok"] is True
    assert body["tool"] == "qbt_offload_candidates"
    assert body["data"]["count"] == 0


def test_resume_custom_tag_still_requires_confirmation(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(qbt.torrents_resume(settings, tag="archive", confirm=False))
    assert body["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_media_stage_exclusions_match_configured_labels_only(mcp_settings) -> None:
    settings = replace(mcp_settings, qbt_media_stage_excluded_labels=frozenset({"retain"}))
    assert qbt._media_stage_owner_excluded(settings, {"category": "movies", "tags": "retain,urgent"})
    assert not qbt._media_stage_owner_excluded(settings, {"category": "movies", "tags": "priority"})
