from homelab_workers.shared.seerr import normalize_seerr_request


def test_normalize_series_request_keeps_external_ids_and_seasons():
    row = {
        "id": 1234,
        "status": 2,
        "createdAt": "2026-08-16T02:00:00.000Z",
        "media": {
            "mediaType": "tv",
            "title": "Stag",
            "firstAirDate": "2024-01-01",
            "tmdbId": 999,
            "tvdbId": 4444,
            "imdbId": "tt1234567",
            "externalServiceId": 243,
        },
        "seasons": [{"seasonNumber": 1}, {"seasonNumber": 2}],
    }

    req = normalize_seerr_request(row)

    assert req is not None
    assert req.request_id == 1234
    assert req.media_type == "series"
    assert req.title == "Stag"
    assert req.year == 2024
    assert req.seasons == (1, 2)
    assert req.stable_ids.tmdb_id == 999
    assert req.stable_ids.tvdb_id == 4444
    assert req.stable_ids.imdb_id == "tt1234567"
    assert req.arr_id == 243


def test_normalize_movie_request_uses_tmdb_and_imdb():
    row = {
        "id": 55,
        "status": "approved",
        "media": {
            "mediaType": "movie",
            "title": "Example Movie",
            "releaseDate": "2025-05-04",
            "tmdbId": 111,
            "imdbId": "tt7654321",
            "externalServiceId": 812,
        },
    }

    req = normalize_seerr_request(row)

    assert req is not None
    assert req.media_type == "movie"
    assert req.title == "Example Movie"
    assert req.year == 2025
    assert req.seasons == ()
    assert req.stable_ids.tmdb_id == 111
    assert req.stable_ids.imdb_id == "tt7654321"
    assert req.arr_id == 812


def test_normalize_ignores_unknown_media_type():
    assert normalize_seerr_request({"id": 1, "media": {"mediaType": "person"}}) is None
