from homelab_workers.shared.seerr_reconciler_models import (
    NormalizedSeerrRequest,
    ReleaseEvidence,
    StableIds,
)
from homelab_workers.shared.seerr_reconciler_policy import (
    candidate_identity_confidence,
    classify_rejections,
    fallback_allowed,
)


def _series_request() -> NormalizedSeerrRequest:
    return NormalizedSeerrRequest(
        request_id=1,
        media_type="series",
        title="Stag",
        year=2024,
        seasons=(1,),
        stable_ids=StableIds(tmdb_id=123, tvdb_id=456, imdb_id="tt1111111"),
        arr_id=243,
        status="approved",
    )


def test_classify_soft_and_hard_rejections():
    assert classify_rejections(["Release is over maximum size"]) == ("soft_size",)
    assert classify_rejections(["Multi-season packs are not supported"]) == ("hard_importability",)
    assert classify_rejections(["Wrong season"]) == ("hard_identity",)
    assert classify_rejections(["Unknown series"]) == ("hard_identity",)
    assert classify_rejections(["Unknown movie"]) == ("hard_identity",)
    assert classify_rejections(["Unknown custom format complaint"]) == ("unknown",)


def test_candidate_confidence_uses_title_and_season():
    candidate = ReleaseEvidence(
        title="Stag S01 1080p WEB-DL",
        guid="guid-1",
        indexer_id=7,
        size_bytes=123,
        seeders=4,
    )

    assert candidate_identity_confidence(_series_request(), candidate) >= 80


def test_candidate_confidence_does_not_match_season_prefix():
    candidate = ReleaseEvidence(
        title="Stag Season 10 1080p WEB-DL",
        guid="guid-season-10",
        indexer_id=7,
        size_bytes=123,
        seeders=4,
    )

    assert candidate_identity_confidence(_series_request(), candidate) < 80


def test_fallback_blocks_without_stable_ids():
    request = NormalizedSeerrRequest(
        request_id=1,
        media_type="series",
        title="Stag",
        year=None,
        seasons=(1,),
        stable_ids=StableIds(),
        arr_id=243,
        status="approved",
    )

    allowed, reason, candidate = fallback_allowed(request, [], set())

    assert allowed is False
    assert reason == "stable external id required"
    assert candidate is None


def test_fallback_selects_soft_rejected_seeded_candidate():
    candidate = ReleaseEvidence(
        title="Stag S01 1080p WEB-DL",
        guid="guid-1",
        indexer_id=7,
        size_bytes=1000,
        seeders=3,
        rejections=("Release is over maximum size",),
    )

    allowed, reason, selected = fallback_allowed(_series_request(), [candidate], set())

    assert allowed is True
    assert reason == "soft override allowed"
    assert selected == candidate


def test_fallback_blocks_hard_rejection_and_failed_guid():
    hard = ReleaseEvidence(
        title="Stag S01-S02 1080p",
        guid="guid-hard",
        indexer_id=7,
        size_bytes=1000,
        seeders=3,
        rejections=("Multi-season packs are not supported",),
    )
    failed = ReleaseEvidence(
        title="Stag S01 1080p",
        guid="guid-failed",
        indexer_id=7,
        size_bytes=1000,
        seeders=3,
        rejections=("Release is over maximum size",),
    )

    assert fallback_allowed(_series_request(), [hard], set())[0] is False
    assert fallback_allowed(_series_request(), [failed], {"guid-failed"})[0] is False


def test_fallback_requires_every_requested_season():
    request = _series_request()
    request = NormalizedSeerrRequest(
        request_id=request.request_id,
        media_type=request.media_type,
        title=request.title,
        year=request.year,
        seasons=(1, 2),
        stable_ids=request.stable_ids,
        arr_id=request.arr_id,
        status=request.status,
    )
    season_one_only = ReleaseEvidence(
        title="Stag S01 2024 1080p WEB-DL",
        guid="guid-s01",
        indexer_id=7,
        size_bytes=1000,
        seeders=3,
        rejections=("Release is over maximum size",),
    )

    allowed, reason, selected = fallback_allowed(request, [season_one_only], set())

    assert allowed is False
    assert reason == "candidate identity confidence too low"
    assert selected is None


def test_fallback_blocks_previously_failed_hash():
    candidate = ReleaseEvidence(
        title="Stag S01 2024 1080p WEB-DL",
        guid="guid-new",
        indexer_id=7,
        size_bytes=1000,
        seeders=3,
        rejections=("Release is over maximum size",),
        raw={"infoHash": "hash-failed"},
    )

    allowed, reason, selected = fallback_allowed(
        _series_request(), [candidate], set(), {"hash-failed"}
    )

    assert allowed is False
    assert reason == "candidate already failed"
    assert selected is None


def test_fallback_blocks_previously_failed_hash_case_insensitively():
    candidate = ReleaseEvidence(
        title="Stag S01 2024 1080p WEB-DL",
        guid="guid-new-case",
        indexer_id=7,
        size_bytes=1000,
        seeders=3,
        rejections=("Release is over maximum size",),
        raw={"infoHash": "AbCdEf123"},
    )

    allowed, reason, selected = fallback_allowed(
        _series_request(), [candidate], set(), {"abcdef123"}
    )

    assert allowed is False
    assert reason == "candidate already failed"
    assert selected is None
