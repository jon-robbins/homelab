from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from homelab_mcp.tools.mutations import media_downloads


def test_radarr_release_grab_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        media_downloads.radarr_release_grab(
            settings,
            movie_id=42,
            guid="g1",
            size_bytes=1_000,
            expected_action="grab:radarr:42:999",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"


@patch("homelab_mcp.tools.mutations.media_downloads.grab_radarr_release")
@patch("homelab_mcp.tools.mutations.media_downloads.ArrClient")
def test_radarr_release_grab_applies(mock_cls, mock_grab, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_grab.return_value = {"ok": True, "app": "radarr", "command": {"id": 1}}
    body = json.loads(
        media_downloads.radarr_release_grab(
            settings,
            movie_id=42,
            guid="g1",
            size_bytes=1_000,
            expected_action="grab:radarr:42:1000",
        )
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    assert body["data"]["app"] == "radarr"


def test_sonarr_release_grab_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        media_downloads.sonarr_release_grab(
            settings,
            episode_id=99,
            guid="g1",
            size_bytes=500,
            expected_action="wrong",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"


@patch("homelab_mcp.tools.mutations.media_downloads.grab_sonarr_release")
@patch("homelab_mcp.tools.mutations.media_downloads.ArrClient")
def test_sonarr_release_grab_applies(mock_cls, mock_grab, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_grab.return_value = {"ok": True, "app": "sonarr", "command": {"id": 2}}
    body = json.loads(
        media_downloads.sonarr_release_grab(
            settings,
            episode_id=99,
            guid="g1",
            size_bytes=500,
            expected_action="grab:sonarr:99:500",
        )
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True


def test_prowlarr_indexer_grab_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        media_downloads.prowlarr_indexer_grab(
            settings,
            indexer_id=7,
            guid="g1",
            size_bytes=100,
            expected_action="grab:prowlarr:7:99",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"


@patch("homelab_mcp.tools.mutations.media_downloads.grab_prowlarr_release")
@patch("homelab_mcp.tools.mutations.media_downloads.ProwlarrClient")
def test_prowlarr_indexer_grab_applies(mock_cls, mock_grab, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_grab.return_value = {"ok": True, "source": "prowlarr", "prowlarr": {}}
    body = json.loads(
        media_downloads.prowlarr_indexer_grab(
            settings,
            indexer_id=7,
            guid="g1",
            size_bytes=100,
            expected_action="grab:prowlarr:7:100",
            title="ROM Set",
        )
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    mock_grab.assert_called_once()
    assert mock_grab.call_args.kwargs["title"] == "ROM Set"


@patch("homelab_mcp.tools.mutations.media_downloads.grab_prowlarr_release")
@patch("homelab_mcp.tools.mutations.media_downloads.ProwlarrClient")
def test_prowlarr_indexer_grab_not_cached(mock_cls, mock_grab, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_grab.return_value = {
        "ok": False,
        "error": {
            "code": "RELEASE_NOT_CACHED",
            "message": "Prowlarr no longer has this release cached; run prowlarr_indexer_search again.",
        },
    }
    body = json.loads(
        media_downloads.prowlarr_indexer_grab(
            settings,
            indexer_id=7,
            guid="g1",
            size_bytes=100,
            expected_action="grab:prowlarr:7:100",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "RELEASE_NOT_CACHED"


def test_grab_mutations_disabled(mcp_settings) -> None:
    body = json.loads(
        media_downloads.radarr_release_grab(
            mcp_settings,
            movie_id=42,
            guid="g1",
            size_bytes=1_000,
            expected_action="grab:radarr:42:1000",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "MUTATIONS_DISABLED"
