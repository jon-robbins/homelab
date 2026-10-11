from __future__ import annotations

import json
from unittest.mock import patch

from homelab_mcp.tools import qbt


@patch("homelab_mcp.tools.qbt.QBittorrentClient")
def test_transfer_summary(mock_client_cls, mcp_settings) -> None:
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.transfer_info.return_value = {
        "connection_status": "connected",
        "last_known_external_address": "1.2.3.4",
        "dl_info_speed": 1200,
        "up_info_speed": 300,
        "dl_rate_limit": 0,
        "up_rate_limit": 0,
        "dht_nodes": 0,
    }
    body = json.loads(qbt.transfer_summary(mcp_settings))
    assert body["ok"] is True
    assert body["tool"] == "qbt_transfer_summary"
    assert body["data"]["connection_status"] == "connected"
    assert body["data"]["up_speed_bps"] == 300


@patch("homelab_mcp.tools.qbt.QBittorrentClient")
def test_preferences_snapshot_includes_profile_hint(mock_client_cls, mcp_settings) -> None:
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.app_preferences.return_value = {
        "queueing_enabled": True,
        "max_active_downloads": 20,
        "max_active_uploads": -1,
        "max_connec": -1,
        "max_connec_per_torrent": 15,
        "dl_limit": 5120000,
        "up_limit": 0,
    }
    body = json.loads(qbt.preferences_snapshot(mcp_settings))
    assert body["ok"] is True
    assert body["data"]["profile_hint"] == "custom"


@patch("homelab_mcp.tools.qbt.QBittorrentClient")
def test_torrents_summary_counts(mock_client_cls, mcp_settings) -> None:
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.torrents_info_raw.return_value = [
        {"hash": "a", "progress": 1.0, "state": "stalledUP", "num_leechs": 2, "upspeed": 100, "dlspeed": 0},
        {"hash": "b", "progress": 0.3, "state": "downloading", "num_leechs": 0, "upspeed": 0, "dlspeed": 50},
    ]
    body = json.loads(qbt.torrents_summary(mcp_settings, tag="freeleech"))
    assert body["ok"] is True
    assert body["data"]["total"] == 2
    assert body["data"]["complete"] == 1
    assert body["data"]["actively_downloading"] == 1


@patch("homelab_mcp.tools.qbt.QBittorrentClient")
def test_torrents_list_paginates(mock_client_cls, mcp_settings) -> None:
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.torrents_info_raw.return_value = [
        {"hash": "a", "name": "one", "progress": 0.1, "state": "downloading"},
        {"hash": "b", "name": "two", "progress": 0.2, "state": "downloading"},
    ]
    body = json.loads(qbt.torrents_list(mcp_settings, limit=1, offset=1))
    assert body["ok"] is True
    assert body["data"]["total"] == 2
    assert len(body["data"]["torrents"]) == 1
    assert body["data"]["torrents"][0]["hash"] == "b"


@patch("homelab_mcp.tools.qbt.QBittorrentClient")
def test_torrent_detail_resolves_unique_prefix(mock_client_cls, mcp_settings) -> None:
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.torrents_info_raw.return_value = [
        {"hash": "abcdef1234567890", "name": "match", "progress": 1.0, "state": "stalledUP"},
        {"hash": "fff1111122223333", "name": "other", "progress": 0.5, "state": "downloading"},
    ]
    mock_client.torrent_trackers.return_value = [{"url": "https://tracker", "num_leeches": 3, "num_seeds": 9}]
    mock_client.torrent_peers.return_value = {"peers": {"x": {"ip": "1.1.1.1", "client": "qB"}}}
    body = json.loads(qbt.torrent_detail(mcp_settings, "abcdef12"))
    assert body["ok"] is True
    assert body["data"]["hash"] == "abcdef1234567890"
    assert body["data"]["trackers"][0]["num_leeches"] == 3


@patch("homelab_mcp.tools.qbt.QBittorrentClient")
def test_torrent_detail_caps_peers_and_reports_total(mock_client_cls, mcp_settings) -> None:
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.torrents_info_raw.return_value = [
        {"hash": "abcdef1234567890", "name": "match", "progress": 1.0, "state": "stalledUP"},
    ]
    mock_client.torrent_trackers.return_value = []
    mock_client.torrent_peers.return_value = {
        "peers": {str(i): {"ip": f"192.0.2.{i}"} for i in range(100)}
    }

    body = json.loads(qbt.torrent_detail(mcp_settings, "abcdef12"))

    assert body["data"]["peer_count"] == 100
    assert len(body["data"]["peers"]) < 100


@patch("homelab_mcp.tools.qbt.QBittorrentClient")
def test_torrent_detail_redacts_tracker_passkey(mock_client_cls, mcp_settings) -> None:
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.torrents_info_raw.return_value = [
        {"hash": "abcdef1234567890", "name": "match", "progress": 1.0, "state": "stalledUP"},
    ]
    mock_client.torrent_trackers.return_value = [
        {
            "url": "https://tracker.example/announce/PRIVATEPASS?passkey=SECRET&ok=1",
            "num_leeches": 3,
            "num_seeds": 9,
        }
    ]
    mock_client.torrent_peers.return_value = {"peers": {}}

    raw = qbt.torrent_detail(mcp_settings, "abcdef12")
    body = json.loads(raw)

    assert body["ok"] is True
    tracker = body["data"]["trackers"][0]
    assert tracker["host"] == "tracker.example"
    assert tracker["url"] == "https://tracker.example/[redacted]"
    assert "PRIVATEPASS" not in raw
    assert "SECRET" not in raw


@patch("homelab_mcp.tools.qbt.QBittorrentClient")
def test_peer_port_verify_connected(mock_client_cls, mcp_settings) -> None:
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.transfer_info.return_value = {"connection_status": "connected"}
    mock_client.app_preferences.return_value = {"listen_port": 51423}
    body = json.loads(qbt.peer_port_verify(mcp_settings))
    assert body["ok"] is True
    assert body["tool"] == "qbt_peer_port_verify"
    assert body["data"]["healthy"] is True
    assert body["data"]["ports_match"] is True
    assert body["data"]["host_listener_verified"] is None
