from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from homelab_mcp.tools.mutations import autobrr


def test_filter_disable_requires_expected_action(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        autobrr.filter_set_enabled(
            settings,
            filter_name="tl-freeleech-race",
            enabled=False,
            confirm=False,
            expected_action=None,
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"
    assert "filter:disable:tl-freeleech-race" in body["error"]["hint"]


def test_filter_disable_applies_with_expected_action(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    with patch("homelab_mcp.tools.mutations.autobrr.AutobrrClient") as mock_cls:
        mock_cls.return_value.set_filter_enabled.return_value = {
            "id": 1,
            "name": "tl-freeleech-race",
            "enabled": False,
        }
        body = json.loads(
            autobrr.filter_set_enabled(
                settings,
                filter_name="tl-freeleech-race",
                enabled=False,
                expected_action="filter:disable:tl-freeleech-race",
            )
        )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True


def test_irc_restart_requires_confirm(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        autobrr.irc_restart(settings, network="torrentleech", confirm=False)
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "CONFIRMATION_REQUIRED"
