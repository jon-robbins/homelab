from __future__ import annotations

from arr_retry.logic import (
    analyze_releases,
    choose_force_grab_candidate,
    is_force_grab_eligible_rejection,
    queue_item_looks_stalled,
)


def test_is_force_grab_eligible_rejection_accepts_supported_reasons() -> None:
    assert is_force_grab_eligible_rejection("Larger than maximum allowed: 3500MB")
    assert is_force_grab_eligible_rejection("Existing file meets cutoff")
    assert is_force_grab_eligible_rejection("Release in queue already meets cutoff")


def test_is_force_grab_eligible_rejection_rejects_unexpected_reasons() -> None:
    assert not is_force_grab_eligible_rejection("Indexer unavailable")
    assert not is_force_grab_eligible_rejection("Unknown quality profile error")


def test_choose_force_grab_candidate_returns_none_when_no_eligible_candidates() -> None:
    releases = [
        {"approved": True, "downloadAllowed": True, "seeders": 15},
        {"approved": False, "downloadAllowed": False, "seeders": 20, "rejections": ["Larger than maximum allowed"]},
        {"approved": False, "downloadAllowed": True, "seeders": 0, "rejections": ["Larger than maximum allowed"]},
        {"approved": False, "downloadAllowed": True, "seeders": 5, "rejections": ["Indexer unavailable"]},
    ]
    assert choose_force_grab_candidate(releases, min_seeders=1) is None


def test_choose_force_grab_candidate_prefers_more_seeders_then_smaller_size() -> None:
    releases = [
        {
            "title": "candidate-a",
            "approved": False,
            "downloadAllowed": True,
            "seeders": 12,
            "size": 4_000_000_000,
            "rejections": ["Larger than maximum allowed"],
        },
        {
            "title": "candidate-b",
            "approved": False,
            "downloadAllowed": True,
            "seeders": 12,
            "size": 3_500_000_000,
            "rejections": ["Larger than maximum allowed"],
        },
        {
            "title": "candidate-c",
            "approved": False,
            "downloadAllowed": True,
            "seeders": 20,
            "size": 5_000_000_000,
            "rejections": ["Larger than maximum allowed"],
        },
    ]
    selected = choose_force_grab_candidate(releases, min_seeders=1)
    assert selected is not None
    assert selected["title"] == "candidate-c"


def test_analyze_releases_distinguishes_approved_vs_seeded() -> None:
    releases = [
        {"approved": False, "downloadAllowed": True, "seeders": 2},
        {"approved": True, "downloadAllowed": True, "seeders": 0},
    ]
    has_approved, has_seeded = analyze_releases(releases, min_seeders=1)
    assert has_approved is True
    assert has_seeded is True


def test_queue_item_looks_stalled_detects_stalled_torrent() -> None:
    stalled_item = {
        "protocol": "torrent",
        "status": "downloading",
        "trackedDownloadStatus": "warning",
        "trackedDownloadState": "importPending",
        "errorMessage": "No peers found",
        "statusMessages": [],
    }
    assert queue_item_looks_stalled(stalled_item) is True


def test_queue_item_looks_stalled_ignores_non_torrent_protocols() -> None:
    non_torrent_item = {
        "protocol": "usenet",
        "status": "downloading",
        "errorMessage": "No peers found",
    }
    assert queue_item_looks_stalled(non_torrent_item) is False
