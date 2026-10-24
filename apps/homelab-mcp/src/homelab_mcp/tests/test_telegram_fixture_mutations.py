from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from homelab_mcp.tools.mutations import media_downloads, qbt


def test_radarr_release_grab_keeps_generic_expected_action_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        media_downloads.radarr_release_grab(
            settings,
            movie_id=42,
            guid="g1",
            size_bytes=1000,
            indexer_id=1,
            expected_action="wrong",
            title="Night of the Living Dead",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"


def test_radarr_release_grab_rejects_invalid_ids_or_empty_guid(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)

    # Invalid movie_id
    body = json.loads(
        media_downloads.radarr_release_grab(
            settings,
            movie_id=-1,
            guid="g1",
            size_bytes=1000,
            expected_action="grab:radarr:-1:1000",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"

    # Empty guid
    body2 = json.loads(
        media_downloads.radarr_release_grab(
            settings,
            movie_id=42,
            guid="",
            size_bytes=1000,
            expected_action="grab:radarr:42:1000",
        )
    )
    assert body2["ok"] is False
    assert body2["error"]["code"] == "INVALID_ARGUMENT"


@patch("homelab_mcp.tools.mutations.media_downloads.grab_radarr_release")
@patch("homelab_mcp.tools.mutations.media_downloads.ArrClient")
def test_radarr_release_grab_includes_mutation_envelope_fields(mock_cls, mock_grab, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_grab.return_value = {"ok": True, "app": "radarr", "command": {"id": 10}}
    body = json.loads(
        media_downloads.radarr_release_grab(
            settings,
            movie_id=42,
            guid="https://thepiratebay.org/description.php?id=999999",
            size_bytes=1000,
            indexer_id=2,
            expected_action="grab:radarr:42:1000",
            title="Night of the Living Dead",
        )
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    assert body["data"]["action"] == "grab"
    assert "target" in body["data"]
    assert body["data"]["applied"] == {"id": 10}


def test_arr_queue_remove_rejects_invalid_queue_id(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        media_downloads.arr_queue_remove(
            settings,
            media_type="movie",
            queue_id=-5,
            confirm=True,
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_qbt_pause_resume_rejects_invalid_hash(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body_pause = json.loads(
        qbt.torrents_pause(
            settings,
            hashes=["short_prefix"],
            expected_action="pause:hashes:1",
        )
    )
    assert body_pause["ok"] is False
    assert body_pause["error"]["code"] == "INVALID_ARGUMENT"

    body_resume = json.loads(
        qbt.torrents_resume(
            settings,
            hashes=["z" * 40],
            confirm=True,
        )
    )
    assert body_resume["ok"] is False
    assert body_resume["error"]["code"] == "INVALID_ARGUMENT"


def test_qbt_force_start_rejects_invalid_hash(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        qbt.qbt_torrent_force_start(
            settings,
            torrent_hash="invalid-hash",
            confirm=True,
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"
