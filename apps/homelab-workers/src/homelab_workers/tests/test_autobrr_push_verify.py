from homelab_workers.shared.autobrr import verify_recent_pushes_in_qbt


def test_verify_recent_pushes_matches_by_normalized_name() -> None:
    releases = [
        {
            "name": "The Conformist 1970 REMASTERED 1080p BluRay x264-OLDTiME",
            "filter_status": "FILTER_APPROVED",
            "action_status": [{"status": "PUSH_APPROVED"}],
            "normalized_hash": "e5737d496c05f80023fd1d9371186af0",
            "timestamp": "2026-06-28T12:00:00+00:00",
        },
    ]
    qbt_torrents = [
        {
            "hash": "64555e6ac568bd47177d6ef36cbac6a4f5f50335",
            "name": "The.Conformist.1970.REMASTERED.1080p.BluRay.x264-OLDTiME",
        }
    ]
    result = verify_recent_pushes_in_qbt(
        releases,
        qbt_torrents=qbt_torrents,
        recent_limit=10,
    )
    assert result["checked"] == 1
    assert result["missing_in_qbt"] == []


def test_verify_recent_pushes_matches_generation_war_season_alias() -> None:
    releases = [
        {
            "name": "Generation War 2013 S01 1080p BluRay x265 HEVC 10bit AAC 5 1-Garshasp",
            "filter_status": "FILTER_APPROVED",
            "action_status": [{"status": "PUSH_APPROVED"}],
            "normalized_hash": "d276aa3f2eb5e3456b7af7b9be477f2d",
            "timestamp": "2026-07-14T00:19:08+02:00",
        },
    ]
    qbt_torrents = [
        {
            "hash": "fd0d09d7fe61fbddec52fe00cbf0d7dd02ac6888",
            "name": "Generation War (2013) Season 1 S01 (1080p BluRay x265 HEVC 10bit AAC 5.1 Garshasp)",
        }
    ]
    result = verify_recent_pushes_in_qbt(
        releases,
        qbt_torrents=qbt_torrents,
        recent_limit=10,
    )
    assert result["checked"] == 1
    assert result["missing_in_qbt"] == []


def test_verify_recent_pushes_matches_backrooms_with_mkv_suffix() -> None:
    releases = [
        {
            "name": "Backrooms 2026 2160p iT WEB-DL DDP5 1 Atmos DV HDR H 265-BYNDR",
            "filter_status": "FILTER_APPROVED",
            "action_status": [{"status": "PUSH_APPROVED"}],
            "normalized_hash": "7f355baa1f88a6fd00ba7aff779fac70",
            "timestamp": "2026-07-14T01:35:44+02:00",
        },
    ]
    qbt_torrents = [
        {
            "hash": "f4c641bfa892bcd0705865c082d07641e7d085a8",
            "name": "Backrooms.2026.2160p.iT.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-BYNDR.mkv",
        }
    ]
    result = verify_recent_pushes_in_qbt(
        releases,
        qbt_torrents=qbt_torrents,
        recent_limit=10,
    )
    assert result["checked"] == 1
    assert result["missing_in_qbt"] == []


def test_verify_recent_pushes_flags_missing_hash() -> None:
    releases = [
        {
            "name": "In qBT",
            "filter_status": "FILTER_APPROVED",
            "action_status": [{"status": "PUSH_APPROVED"}],
            "normalized_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "timestamp": "2026-06-28T12:00:00+00:00",
        },
        {
            "name": "Missing",
            "filter_status": "FILTER_APPROVED",
            "action_status": [{"status": "PUSH_APPROVED"}],
            "normalized_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "timestamp": "2026-06-28T11:00:00+00:00",
        },
        {
            "name": "Rejected",
            "filter_status": "FILTER_REJECTED",
            "action_status": [{"status": "PUSH_REJECTED"}],
            "normalized_hash": "cccccccccccccccccccccccccccccccc",
            "timestamp": "2026-06-28T10:00:00+00:00",
        },
    ]
    qbt_torrents = [{"hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "name": "In qBT"}]
    result = verify_recent_pushes_in_qbt(
        releases,
        qbt_torrents=qbt_torrents,
        recent_limit=10,
    )
    assert result["checked"] == 2
    assert len(result["missing_in_qbt"]) == 1
    assert result["missing_in_qbt"][0]["name"] == "Missing"
    assert result["missing_in_qbt"][0]["normalized_hash"] == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
