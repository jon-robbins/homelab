from homelab_workers.shared.seerr_reconciler import diagnose_request, shape_release
from homelab_workers.shared.seerr_reconciler_models import NormalizedSeerrRequest, StableIds


def _request() -> NormalizedSeerrRequest:
    return NormalizedSeerrRequest(
        request_id=44,
        media_type="series",
        title="Stag",
        year=2024,
        seasons=(1,),
        stable_ids=StableIds(tmdb_id=100, tvdb_id=200),
        arr_id=243,
        status="approved",
    )


def test_shape_release_preserves_rejections_and_seeders():
    release = shape_release(
        {
            "title": "Stag S01 1080p",
            "guid": "guid",
            "indexer_id": 7,
            "size_bytes": 123,
            "seeders": 5,
            "rejections": ["Release is over maximum size"],
            "download_allowed": False,
        }
    )

    assert release.title == "Stag S01 1080p"
    assert release.rejections == ("Release is over maximum size",)
    assert release.seeders == 5


def test_diagnose_prefers_arr_downloadable_release():
    result = diagnose_request(
        _request(),
        arr_status={"in_library": True, "monitored": True},
        arr_releases=[
            {
                "title": "Stag S01 1080p",
                "guid": "arr-guid",
                "indexer_id": 7,
                "size_bytes": 123,
                "seeders": 1,
                "download_allowed": True,
            }
        ],
        prowlarr_releases=[],
        qbt_torrents=[],
        state={"requests": {}},
    )

    assert result.state == "arr_monitored"
    assert result.recommended_action == "grab_arr_release"
    assert result.expected_action == "grab:arr:arr-guid"


def test_diagnose_finds_fallback_candidate_for_soft_rejection():
    result = diagnose_request(
        _request(),
        arr_status={"in_library": True, "monitored": True},
        arr_releases=[
            {
                "title": "Stag S01 1080p",
                "guid": "arr-rejected",
                "indexer_id": 7,
                "size_bytes": 123,
                "seeders": 2,
                "download_allowed": False,
                "rejections": ["Release is over maximum size"],
            }
        ],
        prowlarr_releases=[
            {
                "title": "Stag S01 1080p",
                "guid": "prow-guid",
                "indexer_id": 7,
                "size_bytes": 123,
                "seeders": 2,
                "rejections": ["Release is over maximum size"],
            }
        ],
        qbt_torrents=[],
        state={"requests": {}},
    )

    assert result.state == "fallback_candidate_found"
    assert result.recommended_action == "grab_fallback"
    assert result.expected_action == "grab:prowlarr:7:prow-guid"


def test_diagnose_blocks_fallback_after_hard_arr_rejection():
    result = diagnose_request(
        _request(),
        arr_status={"in_library": True, "monitored": True},
        arr_releases=[
            {
                "title": "Stag S01 1080p",
                "guid": "arr-rejected",
                "indexer_id": 7,
                "size_bytes": 123,
                "seeders": 2,
                "download_allowed": False,
                "rejections": ["Multi-season packs are not supported"],
            }
        ],
        prowlarr_releases=[
            {
                "title": "Stag S01 1080p",
                "guid": "prow-guid",
                "indexer_id": 7,
                "size_bytes": 123,
                "seeders": 2,
            }
        ],
        qbt_torrents=[],
        state={"requests": {}},
    )

    assert result.state == "blocked_hard_rejection"
    assert result.reason == "Arr hard rejection"
    assert result.recommended_action == "manual_review"


def test_diagnose_blocks_fallback_without_arr_soft_rejection_evidence():
    result = diagnose_request(
        _request(),
        arr_status={"in_library": True, "monitored": True},
        arr_releases=[],
        prowlarr_releases=[
            {
                "title": "Stag S01 1080p",
                "guid": "prow-guid",
                "indexer_id": 7,
                "size_bytes": 123,
                "seeders": 2,
            }
        ],
        qbt_torrents=[],
        state={"requests": {}},
    )

    assert result.state == "blocked_no_candidate"
    assert result.reason == "no Arr soft rejection evidence"
    assert result.recommended_action == "manual_review"


def test_diagnose_reports_qbt_downloading_from_state_hash():
    result = diagnose_request(
        _request(),
        arr_status={"in_library": True, "monitored": True},
        arr_releases=[],
        prowlarr_releases=[],
        qbt_torrents=[
            {"hash": "abc", "name": "Stag S01", "progress": 0.5, "state": "downloading"}
        ],
        state={"requests": {"44:series:1": {"hash": "abc"}}},
    )

    assert result.state == "downloading"
    assert result.reason == "qBittorrent torrent is active"


def test_diagnose_waits_for_arr_import_after_qbt_completion():
    result = diagnose_request(
        _request(),
        arr_status={"has_file": False, "in_library": True, "monitored": True},
        arr_releases=[],
        prowlarr_releases=[],
        qbt_torrents=[
            {"hash": "abc", "name": "Stag S01", "progress": 0.999, "state": "uploading"}
        ],
        state={"requests": {"44:series:1": {"hash": "abc"}}},
    )

    assert result.state == "downloading"
    assert result.reason == "qBittorrent torrent completed; waiting for Arr import"


def test_diagnose_blocks_fallback_with_previously_failed_hash():
    result = diagnose_request(
        _request(),
        arr_status={"in_library": True, "monitored": True},
        arr_releases=[
            {
                "title": "Stag S01 1080p",
                "guid": "arr-rejected",
                "indexer_id": 7,
                "size_bytes": 123,
                "seeders": 2,
                "download_allowed": False,
                "rejections": ["Release is over maximum size"],
            }
        ],
        prowlarr_releases=[
            {
                "title": "Stag S01 1080p",
                "guid": "new-guid",
                "indexer_id": 7,
                "size_bytes": 123,
                "seeders": 2,
                "hash": "ABC123",
            }
        ],
        qbt_torrents=[],
        state={"requests": {"44:series:1": {"failed_hashes": ["abc123"]}}},
    )

    assert result.state == "blocked_hard_rejection"
    assert result.reason == "candidate already failed"
