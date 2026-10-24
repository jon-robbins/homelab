from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from homelab_mcp.tools import autobrr


@patch("homelab_mcp.tools.autobrr.AutobrrClient")
def test_operational_summary(mock_client_cls, mcp_settings) -> None:
    client = mock_client_cls.return_value
    client.filters.return_value = [
        {
            "name": "tl-freeleech-race",
            "enabled": True,
            "max_downloads": 5,
            "max_downloads_unit": "DAY",
        }
    ]
    client.irc_networks.return_value = [
        {
            "name": "TorrentLeech.org",
            "healthy": True,
            "connected": True,
            "connection_errors": [],
            "auth": {"mechanism": "NONE"},
            "channels": [{"name": "#tlannounces", "monitoring": True, "last_announce": "x"}],
        }
    ]
    client.releases.return_value = [
        {
            "name": "Example",
            "filter": "tl-freeleech-race",
            "filter_status": "FILTER_APPROVED",
            "action_status": [{"status": "PUSH_APPROVED"}],
            "timestamp": "2026-06-28T15:42:01+02:00",
            "size": 1_000_000_000,
            "category": "Movies :: BlurayRip",
        }
    ]
    body = json.loads(autobrr.operational_summary(mcp_settings, recent_limit=5))
    assert body["ok"] is True
    assert body["tool"] == "autobrr_operational_summary"
    assert body["data"]["irc"][0]["connected"] is True
    assert body["data"]["irc"][0]["auth_mechanism"] == "NONE"
    assert body["data"]["irc"][0]["channels"][0]["name"] == "#tlannounces"
    assert body["data"]["filters"][0]["name"] == "tl-freeleech-race"
    assert body["data"]["recent"][0]["push_status"] == "PUSH_APPROVED"


@patch("homelab_mcp.tools.autobrr.probe_service")
@patch("homelab_mcp.tools.autobrr.QBittorrentClient")
@patch("homelab_mcp.tools.autobrr.AutobrrClient")
def test_pipeline_diagnosis_returns_causes(
    mock_autobrr_cls, mock_qbt_cls, mock_probe, mcp_settings
) -> None:
    client = mock_autobrr_cls.return_value
    client.filters.return_value = [
        {"name": "tl-freeleech-race", "enabled": True, "max_downloads": 5, "max_downloads_unit": "DAY"}
    ]
    client.irc_networks.return_value = [
        {
            "name": "TorrentLeech.org",
            "healthy": True,
            "connected": True,
            "connection_errors": [],
            "auth": {"mechanism": "NONE"},
            "channels": [{"name": "#tlannounces", "monitoring": True, "last_announce": "x"}],
        }
    ]
    client.releases.return_value = []
    mock_probe.side_effect = [
        {"reachable": True, "status": "healthy", "service": "autobrr"},
        {"reachable": True, "status": "healthy", "service": "qbittorrent"},
    ]
    qbt = MagicMock()
    qbt.__enter__.return_value = qbt
    qbt.__exit__.return_value = None
    qbt.torrents_info_raw.return_value = []
    mock_qbt_cls.return_value = qbt

    body = json.loads(autobrr.pipeline_diagnosis(mcp_settings))
    assert body["ok"] is True
    assert body["tool"] == "autobrr_pipeline_diagnosis"
    assert "summary" in body["data"]
    assert "likely_causes" in body["data"]
    assert "push_verify" in body["data"]


def _healthy_pipeline(mock_autobrr_cls, mock_qbt_cls, mock_probe) -> None:
    client = mock_autobrr_cls.return_value
    client.filters.return_value = []
    client.irc_networks.return_value = []
    client.releases.return_value = []
    mock_probe.side_effect = [
        {"reachable": True, "status": "healthy", "service": "autobrr"},
        {"reachable": True, "status": "healthy", "service": "qbittorrent"},
    ]
    qbt = MagicMock()
    qbt.__enter__.return_value = qbt
    qbt.torrents_info_raw.return_value = []
    mock_qbt_cls.return_value = qbt


@patch("homelab_mcp.tools.autobrr.probe_service")
@patch("homelab_mcp.tools.autobrr.QBittorrentClient")
@patch("homelab_mcp.tools.autobrr.AutobrrClient")
def test_pipeline_diagnosis_folds_in_extension_checks(
    mock_autobrr_cls, mock_qbt_cls, mock_probe, mcp_settings
) -> None:
    _healthy_pipeline(mock_autobrr_cls, mock_qbt_cls, mock_probe)

    def tracker_session() -> dict:
        return {
            "name": "tracker_session",
            "health": {"http_status": 401},
            "causes": [["SESSION_EXPIRED", "Refresh the session."]],
        }

    body = json.loads(autobrr.pipeline_diagnosis(mcp_settings, checks=[tracker_session]))
    assert body["ok"] is True, body
    assert body["data"]["health"]["tracker_session"] == {"http_status": 401}
    assert body["data"]["likely_causes"] == ["SESSION_EXPIRED"]
    assert body["data"]["recommendations"] == ["Refresh the session."]


@patch("homelab_mcp.tools.autobrr.probe_service")
@patch("homelab_mcp.tools.autobrr.QBittorrentClient")
@patch("homelab_mcp.tools.autobrr.AutobrrClient")
def test_pipeline_diagnosis_survives_failing_extension_check(
    mock_autobrr_cls, mock_qbt_cls, mock_probe, mcp_settings
) -> None:
    _healthy_pipeline(mock_autobrr_cls, mock_qbt_cls, mock_probe)

    def broken_check() -> dict:
        raise RuntimeError("boom")

    body = json.loads(autobrr.pipeline_diagnosis(mcp_settings, checks=[broken_check]))
    assert body["ok"] is True, body
    assert body["data"]["health"]["broken_check"]["reachable"] is False
    assert body["data"]["likely_causes"] == []
