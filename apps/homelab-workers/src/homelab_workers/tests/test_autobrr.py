from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from homelab_workers.shared.autobrr import (
    build_operational_summary,
    compact_irc_network,
    compact_release,
    extract_rejection_messages,
    quota_for_filter,
    summarize_releases,
)


def _release(
    *,
    name: str,
    filter_name: str = "example-freeleech-race",
    filter_status: str = "FILTER_APPROVED",
    push_status: str = "PUSH_APPROVED",
    timestamp: str,
    size: int = 1_000_000_000,
) -> dict:
    return {
        "name": name,
        "filter": filter_name,
        "filter_status": filter_status,
        "action_status": [{"status": push_status}],
        "timestamp": timestamp,
        "size": size,
    }


def test_summarize_releases_counts_push_and_filter() -> None:
    since = datetime(2026, 6, 28, 0, 0, tzinfo=ZoneInfo("UTC"))
    releases = [
        _release(name="a", timestamp="2026-06-28T10:00:00+00:00"),
        _release(
            name="b",
            timestamp="2026-06-28T11:00:00+00:00",
            filter_status="FILTER_REJECTED",
            push_status="PUSH_REJECTED",
        ),
        _release(name="old", timestamp="2026-06-27T23:00:00+00:00"),
    ]
    counts = summarize_releases(releases, since=since)
    assert counts["total"] == 2
    assert counts["filter_approved"] == 1
    assert counts["filter_rejected"] == 1
    assert counts["push_approved"] == 1
    assert counts["push_rejected"] == 1


def test_quota_for_filter_remaining() -> None:
    since = datetime(2026, 6, 28, 0, 0, tzinfo=timezone.utc)
    releases = [
        _release(name=f"grab-{idx}", timestamp=f"2026-06-28T1{idx}:00:00+00:00")
        for idx in range(3)
    ]
    quota = quota_for_filter(
        releases,
        filter_name="example-freeleech-race",
        since=since,
        max_downloads=5,
    )
    assert quota["used_today"] == 3
    assert quota["remaining_today"] == 2
    assert quota["at_quota"] is False


def test_build_operational_summary_includes_recent() -> None:
    summary = build_operational_summary(
        filters=[
            {
                "name": "example-freeleech-race",
                "enabled": True,
                "max_downloads": 5,
                "max_downloads_unit": "DAY",
            }
        ],
        irc_networks=[{"name": "irc.example.org", "healthy": True, "connected": True}],
        releases=[
            _release(name="newer", timestamp="2026-06-28T15:00:00+02:00"),
            _release(name="older", timestamp="2026-06-28T12:00:00+02:00"),
        ],
        tz_name="UTC",
        recent_limit=1,
    )
    assert summary["irc"][0]["healthy"] is True
    assert summary["filters"][0]["name"] == "example-freeleech-race"
    assert len(summary["recent"]) == 1
    assert summary["recent"][0]["name"] == "newer"


def test_extract_rejection_messages_from_release_and_action() -> None:
    release = {
        "rejections": [{"reason": "size", "message": "too small"}],
        "action_status": [
            {
                "status": "PUSH_ERROR",
                "rejections": [{"message": "qbt connection refused"}],
            }
        ],
    }
    msgs = extract_rejection_messages(release)
    assert msgs == ["too small", "qbt connection refused"]


def test_compact_release_includes_rejections_when_present() -> None:
    release = {
        "name": "Example",
        "filter": "example-freeleech-race",
        "filter_status": "FILTER_REJECTED",
        "action_status": [],
        "timestamp": "2026-06-28T10:00:00+00:00",
        "size": 1,
        "rejections": [{"message": "wrong category"}],
    }
    compact = compact_release(release)
    assert compact["rejections"] == ["wrong category"]


def test_compact_release_omits_empty_rejections() -> None:
    release = {
        "name": "Ok",
        "filter": "example-freeleech-race",
        "filter_status": "FILTER_APPROVED",
        "action_status": [{"status": "PUSH_APPROVED"}],
        "timestamp": "2026-06-28T10:00:00+00:00",
        "size": 1_000_000_000,
        "rejections": [],
    }
    compact = compact_release(release)
    assert "rejections" not in compact


def test_compact_irc_network_includes_channels() -> None:
    net = {
        "name": "irc.example.org",
        "connected": True,
        "healthy": True,
        "channels": [
            {
                "name": "#tlannounces",
                "monitoring": True,
                "last_announce": "2026-06-28T16:37:21+02:00",
            }
        ],
    }
    compact = compact_irc_network(net)
    assert compact["channels"][0]["name"] == "#tlannounces"
    assert compact["channels"][0]["last_announce"] == "2026-06-28T16:37:21+02:00"


def test_compact_irc_network_includes_auth_mechanism() -> None:
    net = {
        "name": "irc.example.org",
        "connected": True,
        "healthy": True,
        "auth": {"mechanism": "NONE"},
        "channels": [],
    }
    compact = compact_irc_network(net)
    assert compact["auth_mechanism"] == "NONE"


def test_compact_irc_network_omits_auth_mechanism_when_missing() -> None:
    net = {
        "name": "irc.example.org",
        "connected": True,
        "healthy": True,
        "channels": [],
    }
    compact = compact_irc_network(net)
    assert "auth_mechanism" not in compact
