from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from homelab_mcp.tools.plex import (
    PlexClient,
    media_visibility_diagnosis,
    plex_library_search,
    plex_watch_status,
)

PLEX_XML_MOVIE_INCEPTION = b"""\
<MediaContainer size="1">
  <Video ratingKey="12345" key="/library/metadata/12345" type="movie" title="Inception" year="2010">
    <Media id="99">
      <Part id="101" file="/data/plex/movies/Inception (2010)/Inception (2010).mkv"/>
    </Media>
  </Video>
</MediaContainer>
"""

PLEX_XML_MOVIE_MATCHED = b"""\
<MediaContainer size="1">
  <Video ratingKey="999" key="/library/metadata/999" type="movie" title="MatchedTitle" year="2024">
    <Media id="1">
      <Part id="2" file="/data/plex/movies/MatchedTitle (2024)/MatchedTitle (2024).mkv"/>
    </Media>
  </Video>
</MediaContainer>
"""

PLEX_XML_AMBIGUOUS_TWO_YEARS = b"""\
<MediaContainer size="2">
  <Video ratingKey="1" key="/library/metadata/1" type="movie" title="Ambiguous" year="2023">
    <Media id="10"><Part id="11" file="/a/Ambiguous 2023.mkv"/></Media>
  </Video>
  <Video ratingKey="2" key="/library/metadata/2" type="movie" title="Ambiguous" year="2024">
    <Media id="20"><Part id="21" file="/a/Ambiguous 2024.mkv"/></Media>
  </Video>
</MediaContainer>
"""

PLEX_XML_SHOW = b"""\
<MediaContainer size="1">
  <Directory ratingKey="500" key="/library/metadata/500" type="show" title="Breaking Bad" year="2008"/>
</MediaContainer>
"""

PLEX_XML_EMPTY = b'<MediaContainer size="0"></MediaContainer>'

PLEX_IDENTITY_OK = b'<MediaContainer size="0" friendlyName="Plex"></MediaContainer>'


# ------------------ 1. Basic client constructor sanity (unchanged) ------------------

def test_unconfigured_url_raises() -> None:
    with pytest.raises(ValueError, match="plex_internal_url must be"):
        PlexClient(base_url="", token=None)
    with pytest.raises(ValueError):
        PlexClient(base_url="  ", token="token")


# ------------------ 2. Token non-disclosure: envelope never contains the secret ------------------

def test_token_not_leaked_in_errors(mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="super-secret-plex-token-xyz")
    raw = plex_library_search("Anything", _settings=settings)
    assert isinstance(raw, str)
    # NOTE: Even envelope error strings must NOT contain the secret.
    assert "super-secret-plex-token-xyz" not in raw
    assert "super-secret" not in raw


# ------------------ 3. Exact movie match → envelope OK with 1 match; HEADER auth ------------------

def test_exact_match_returns_file_paths(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="plex-token-123")
    # Item 12: X-Plex-Token is a HEADER, NOT a query param.
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=MatchedTitle&year=2024",
        content=PLEX_XML_MOVIE_MATCHED,
        headers={"Content-Type": "application/xml"},
    )
    raw = plex_library_search(
        "MatchedTitle", year=2024, kind="movie", _settings=settings
    )
    assert isinstance(raw, str)
    env = json.loads(raw)
    assert env["ok"] is True, f"expected ok=True, got {env}"
    matches = env["data"]["matches"]
    assert env["data"]["code"] == "OK"
    assert len(matches) == 1
    match = matches[0]
    assert match["title"] == "MatchedTitle"
    assert match["year"] == 2024
    assert match["kind"] == "movie"
    assert match["file_paths"] == [
        "/data/plex/movies/MatchedTitle (2024)/MatchedTitle (2024).mkv"
    ]
    assert match["rating_key"] == "999"
    # Item 12 (header): verify the captured request HEADER, not URL query
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    req = requests[0]
    assert req.headers.get("X-Plex-Token") == "plex-token-123"
    # Confirm the token NEVER appeared in the URL query string
    assert "X-Plex-Token" not in str(req.url)


def test_show_match_unknown_file_paths_is_not_empty_list(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="plex-token-123")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Breaking+Bad&year=2008",
        content=PLEX_XML_SHOW,
        headers={"Content-Type": "application/xml"},
    )

    env = json.loads(plex_library_search("Breaking Bad", year=2008, _settings=settings))

    assert env["data"]["matches"][0]["file_paths"] is None


# ------------------ 4. Ambiguous title + year filter ------------------

def test_ambiguous_different_years(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="t")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Ambiguous&year=2024",
        content=PLEX_XML_AMBIGUOUS_TWO_YEARS,
        headers={"Content-Type": "application/xml"},
    )
    raw = plex_library_search("Ambiguous", year=2024, kind="any", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is True
    results = env["data"]["matches"]
    assert len(results) == 1
    assert results[0]["year"] == 2024
    assert results[0]["title"] == "Ambiguous"
    requests = httpx_mock.get_requests()
    assert requests[0].headers.get("X-Plex-Token") == "t"


# ------------------ 5. No Plex token configured → UPSTREAM_AUTH error envelope ------------------

def test_no_config_returns_upstream_auth_error(mcp_settings) -> None:
    assert mcp_settings.plex_token is None
    raw = plex_library_search(
        "Anything", year=2020, kind="any", _settings=mcp_settings
    )
    env = json.loads(raw)
    # Result is an envelope error, NOT an empty list.
    assert env["ok"] is False
    assert env["error"]["code"] == "UPSTREAM_AUTH"


# ------------------ 6. PlexClient.is_reachable() with HEADER auth (no query token) ------------------

def test_plex_is_reachable(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/identity",
        content=PLEX_IDENTITY_OK,
        headers={"Content-Type": "application/xml"},
    )
    client = PlexClient(
        base_url=settings.plex_internal_url, token=settings.plex_token
    )
    assert client.is_reachable() is True
    req = httpx_mock.get_requests()[-1]
    assert req.headers.get("X-Plex-Token") == "tok"
    assert "X-Plex-Token" not in str(req.url)

    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/identity",
        status_code=500,
        content=b"boom",
    )
    client2 = PlexClient(
        base_url=settings.plex_internal_url, token=settings.plex_token
    )
    assert client2.is_reachable() is False

    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/identity",
        status_code=200,
        content=b"<OtherTag></OtherTag>",
    )
    client3 = PlexClient(
        base_url=settings.plex_internal_url, token=settings.plex_token
    )
    assert client3.is_reachable() is False


# ---------- Helper: new-style _arr_fn_lookup adapter for visibility tests ----------
# Item 9: Callable[[kind_str, title, year_str], tuple[str, list[dict]]]

def _make_arr_fn(
    *,
    movie_hits: list[dict] | None = None,
    show_hits: list[dict] | None = None,
):
    """Build an _arr_fn_lookup for tests.

    Maps kind "movie"/"show" → hits; any other kind returns empty hits.
    """
    def arr_fn(kind_label: str, title: str, year_str: str) -> tuple[str, list[dict]]:
        if kind_label == "movie":
            return ("movie", list(movie_hits or []))
        if kind_label == "show":
            return ("show", list(show_hits or []))
        return (kind_label, [])
    return arr_fn


# ------------------ 7. Visibility: in_plex_and_arr via envelope + _arr_fn_lookup ------------------

def test_visibility_in_plex_and_arr(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Inception&year=2010",
        content=PLEX_XML_MOVIE_INCEPTION,
        headers={"Content-Type": "application/xml"},
    )
    radarr_hits = [{"title": "Inception", "year": 2010, "tmdbId": 27205, "in_library": True}]
    raw = media_visibility_diagnosis(
        "Inception",
        year=2010,
        kind="movie",
        _settings=settings,
        _arr_fn_lookup=_make_arr_fn(movie_hits=radarr_hits, show_hits=[]),
    )
    env = json.loads(raw)
    assert env["ok"] is True, f"expected ok=True, got {env}"
    d = env["data"]
    assert d["code"] == "OK"
    assert d["stage"] == "in_plex_and_arr"
    assert len(d["in_plex"]) == 1
    assert len(d["in_radarr"]) == 1
    # in_sonarr absent or empty (we passed show_hits=[] so present empty list)
    assert "in_sonarr" not in d or d["in_sonarr"] == []
    assert isinstance(d["notes"], list)
    assert len(d["notes"]) >= 2


# ------------------ 8. Visibility: in_plex_missing_from_arr ------------------

def test_visibility_in_plex_missing_from_arr(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Inception&year=2010",
        content=PLEX_XML_MOVIE_INCEPTION,
        headers={"Content-Type": "application/xml"},
    )
    raw = media_visibility_diagnosis(
        "Inception",
        year=2010,
        kind="movie",
        _settings=settings,
        _arr_fn_lookup=_make_arr_fn(movie_hits=[], show_hits=[]),
    )
    env = json.loads(raw)
    assert env["ok"] is True
    d = env["data"]
    assert d["stage"] == "in_plex_missing_from_arr"
    assert len(d["in_plex"]) == 1


# ------------------ 9. Visibility: in_arr_missing_from_plex ------------------

def test_visibility_in_arr_missing_from_plex(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=MissingShow&year=2022",
        content=PLEX_XML_EMPTY,
        headers={"Content-Type": "application/xml"},
    )
    sonarr_hits = [{"title": "MissingShow", "year": 2022, "tvdbId": 1, "in_library": True}]
    raw = media_visibility_diagnosis(
        "MissingShow",
        year=2022,
        kind="show",
        _settings=settings,
        _arr_fn_lookup=_make_arr_fn(movie_hits=[], show_hits=sonarr_hits),
    )
    env = json.loads(raw)
    assert env["ok"] is True
    d = env["data"]
    assert d["stage"] == "in_arr_missing_from_plex"
    assert d["in_plex"] == []
    assert len(d["in_sonarr"]) == 1


# ------------------ 10. Visibility: missing_from_both ------------------

def test_visibility_missing_from_both(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Nowhere&year=2025",
        content=PLEX_XML_EMPTY,
        headers={"Content-Type": "application/xml"},
    )
    raw = media_visibility_diagnosis(
        "Nowhere",
        year=2025,
        kind="any",
        _settings=settings,
        _arr_fn_lookup=_make_arr_fn(movie_hits=[], show_hits=[]),
    )
    env = json.loads(raw)
    assert env["ok"] is True
    d = env["data"]
    assert d["stage"] == "missing_from_both"
    assert d["in_plex"] == []


# ====================================================================
# NEW TESTS: 5 distinct error codes (NO_MATCH / UPSTREAM_TIMEOUT /
#            UPSTREAM_AUTH / UPSTREAM_ERROR / INVALID_RESPONSE)
# ====================================================================

# ---- 11. NO_MATCH: empty XML → ok_response with code=NO_MATCH ----

def test_plex_search_no_match_code(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=NothingHere",
        content=PLEX_XML_EMPTY,
        headers={"Content-Type": "application/xml"},
    )
    raw = plex_library_search("NothingHere", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is True
    assert env["data"]["code"] == "NO_MATCH"
    assert env["data"]["matches"] == []


# ---- 12. UPSTREAM_TIMEOUT: httpx.TimeoutException → error envelope ----

def test_plex_search_upstream_timeout(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_exception(
        httpx.TimeoutException("connect timeout"),
        method="GET",
        url="http://plex:32400/search?query=Slow",
    )
    raw = plex_library_search("Slow", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is False
    assert env["error"]["code"] == "UPSTREAM_TIMEOUT"


# ---- 13. UPSTREAM_AUTH: HTTP 401 → error envelope ----

def test_plex_search_upstream_auth_401(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="bad-token")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Whatever",
        status_code=401,
        content=b"<html><body>Unauthorized</body></html>",
    )
    raw = plex_library_search("Whatever", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is False
    assert env["error"]["code"] == "UPSTREAM_AUTH"


# ---- 14. UPSTREAM_ERROR: HTTP 500 → error envelope ----

def test_plex_search_upstream_error_500(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Borked",
        status_code=500,
        content=b"Internal Server Error",
    )
    raw = plex_library_search("Borked", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is False
    assert env["error"]["code"] == "UPSTREAM_ERROR"


# ---- 15. INVALID_RESPONSE: malformed XML → error envelope ----

def test_plex_search_invalid_response_malformed_xml(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Garbage",
        content=b"This is not XML at all <<< nope",
        headers={"Content-Type": "application/xml"},
    )
    raw = plex_library_search("Garbage", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is False
    assert env["error"]["code"] == "INVALID_RESPONSE"


# ====================================================================
# VISIBILITY REGRESSION TESTS: Real Arr Candidates, In-Library, Plex Errors
# ====================================================================

def test_visibility_real_arr_candidates_and_in_library(httpx_mock, mcp_settings, monkeypatch) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Dune&year=2021",
        content=PLEX_XML_EMPTY,
        headers={"Content-Type": "application/xml"},
    )

    # Mock media.arr_lookup returning real-shaped envelope with data.candidates
    real_arr_envelope = {
        "ok": True,
        "tool": "arr_lookup",
        "data": {
            "media_type": "movie",
            "query": "Dune",
            "candidates": [
                {
                    "title": "Dune",
                    "year": 2021,
                    "tmdb_id": 438631,
                    "imdb_id": "tt1160419",
                    "in_library": True,
                    "library_id": 10,
                    "has_file": True,
                },
                {
                    "title": "Dune: Part Two",
                    "year": 2024,
                    "tmdb_id": 693134,
                    "imdb_id": "tt15239678",
                    "in_library": False,
                    "library_id": None,
                    "has_file": None,
                },
            ],
        },
        "meta": {"duration_ms": 12},
    }

    import homelab_mcp.tools.media as media_mod
    monkeypatch.setattr(
        media_mod,
        "arr_lookup",
        lambda _s, media_type, query: json.dumps(real_arr_envelope),
    )

    raw = media_visibility_diagnosis("Dune", year=2021, kind="movie", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is True
    d = env["data"]
    assert d["stage"] == "in_arr_missing_from_plex"
    assert d["in_plex"] == []
    assert len(d["in_radarr"]) == 1
    assert d["in_radarr"][0]["title"] == "Dune"
    assert d["in_radarr"][0]["in_library"] is True
    assert d["in_radarr"][0]["has_file"] is True


def test_visibility_in_library_false_is_not_present(httpx_mock, mcp_settings, monkeypatch) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Dune&year=2021",
        content=PLEX_XML_EMPTY,
        headers={"Content-Type": "application/xml"},
    )

    # Candidate has in_library = False -> should not be considered in_arr
    arr_envelope = {
        "ok": True,
        "tool": "arr_lookup",
        "data": {
            "media_type": "movie",
            "query": "Dune",
            "candidates": [
                {
                    "title": "Dune",
                    "year": 2021,
                    "tmdb_id": 438631,
                    "in_library": False,
                    "library_id": None,
                    "has_file": None,
                }
            ],
        },
        "meta": {"duration_ms": 10},
    }

    import homelab_mcp.tools.media as media_mod
    monkeypatch.setattr(
        media_mod,
        "arr_lookup",
        lambda _s, media_type, query: json.dumps(arr_envelope),
    )

    raw = media_visibility_diagnosis("Dune", year=2021, kind="movie", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is True
    d = env["data"]
    assert d["stage"] == "missing_from_both"
    assert d["in_plex"] == []
    assert d["in_radarr"] == []


def test_visibility_plex_failure_returns_error_envelope(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=BrokenPlex",
        status_code=500,
        content=b"Plex crashed",
    )
    raw = media_visibility_diagnosis("BrokenPlex", _settings=settings)
    env = json.loads(raw)
    # Must NOT return ok=True with stage=in_arr_missing_from_plex / missing_from_both
    assert env["ok"] is False
    assert env["error"]["code"] == "UPSTREAM_ERROR"


def test_visibility_plex_timeout_returns_error_envelope(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_exception(
        httpx.TimeoutException("Plex timed out"),
        method="GET",
        url="http://plex:32400/search?query=TimedOut",
    )
    raw = media_visibility_diagnosis("TimedOut", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is False
    assert env["error"]["code"] == "UPSTREAM_TIMEOUT"


def test_visibility_radarr_failure_returns_error_for_movie(httpx_mock, mcp_settings, monkeypatch) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Inception",
        content=PLEX_XML_EMPTY,
        headers={"Content-Type": "application/xml"},
    )
    import homelab_mcp.tools.media as media_mod
    monkeypatch.setattr(
        media_mod,
        "arr_lookup",
        lambda _s, media_type, query: json.dumps({
            "ok": False,
            "tool": "arr_lookup",
            "error": {"code": "UPSTREAM_AUTH", "message": "Radarr API key invalid", "hint": "Check RADARR_API_KEY"},
        }),
    )
    raw = media_visibility_diagnosis("Inception", kind="movie", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is False
    assert env["error"]["code"] == "UPSTREAM_AUTH"


def test_visibility_sonarr_timeout_returns_error_for_show(httpx_mock, mcp_settings, monkeypatch) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Breaking+Bad",
        content=PLEX_XML_EMPTY,
        headers={"Content-Type": "application/xml"},
    )
    import homelab_mcp.tools.media as media_mod
    monkeypatch.setattr(
        media_mod,
        "arr_lookup",
        lambda _s, media_type, query: json.dumps({
            "ok": False,
            "tool": "arr_lookup",
            "error": {"code": "UPSTREAM_TIMEOUT", "message": "Sonarr connection timed out", "hint": "Check network"},
        }),
    )
    raw = media_visibility_diagnosis("Breaking Bad", kind="show", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is False
    assert env["error"]["code"] == "UPSTREAM_TIMEOUT"


def test_visibility_kind_any_fails_if_either_arr_fails(httpx_mock, mcp_settings, monkeypatch) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/search?query=Mystery",
        content=PLEX_XML_EMPTY,
        headers={"Content-Type": "application/xml"},
    )
    import homelab_mcp.tools.media as media_mod

    def fake_arr_lookup(_s, media_type, query):
        if media_type == "series":
            # Sonarr succeeds with 0 matches
            return json.dumps({"ok": True, "tool": "arr_lookup", "data": {"candidates": []}})
        # Radarr fails with 500 upstream error
        return json.dumps({
            "ok": False,
            "tool": "arr_lookup",
            "error": {"code": "UPSTREAM_ERROR", "message": "Radarr 500 Internal Error", "hint": "Check radarr logs"},
        })

    monkeypatch.setattr(media_mod, "arr_lookup", fake_arr_lookup)
    raw = media_visibility_diagnosis("Mystery", kind="any", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is False
    assert env["error"]["code"] == "UPSTREAM_ERROR"


# ====================================================================
# PLEX WATCH STATUS
# ====================================================================

PLEX_ACCOUNTS_JSON = {
    "MediaContainer": {
        "Account": [
            {"id": 806490180, "name": "Quinn Cartwright"},
            {"id": 1, "name": "jrrob8"},
        ]
    }
}

PLEX_SESSIONS_JSON = {
    "MediaContainer": {
        "Metadata": [
            {
                "type": "episode",
                "title": "Do You Take One Another?",
                "grandparentTitle": "90 Day Fiancé",
                "parentTitle": "Season 10",
                "parentIndex": 10,
                "index": 8,
                "viewOffset": 2067759,
                "duration": 5146058,
                "viewCount": 2,
                "lastViewedAt": 1788155443,
                "User": {"title": "Quinn Cartwright"},
                "Player": {"state": "paused", "title": "iPhone", "platform": "iOS"},
            }
        ]
    }
}

PLEX_CONTINUE_WATCHING_JSON = {
    "MediaContainer": {
        "Metadata": [
            {
                "type": "episode",
                "title": "For Richer Or For Poorer",
                "grandparentTitle": "90 Day Fiancé",
                "parentTitle": "Season 10",
                "parentIndex": 10,
                "index": 11,
                "viewOffset": 900000,
                "duration": 5146058,
                "viewCount": 0,
                "lastViewedAt": 1788160000,
            },
            {
                "type": "episode",
                "title": "Do You Take One Another?",
                "grandparentTitle": "90 Day Fiancé",
                "parentTitle": "Season 10",
                "parentIndex": 10,
                "index": 8,
                "viewOffset": 3907000,
                "duration": 5146058,
                "viewCount": 2,
                "lastViewedAt": 1788155443,
            },
        ]
    }
}

PLEX_SEARCH_SHOW_JSON = {
    "MediaContainer": {
        "Metadata": [
            {
                "type": "show",
                "ratingKey": "1097",
                "title": "90 Day Fiancé",
            }
        ]
    }
}

PLEX_SHOW_SEASONS_JSON = {
    "MediaContainer": {
        "Metadata": [
            {"ratingKey": "3745", "index": 10, "title": "Season 10"},
        ]
    }
}

PLEX_SEASON_EPISODES_JSON = {
    "MediaContainer": {
        "Metadata": [
            {
                "index": 7,
                "title": "Speak Now Or Forever Hold Your Peace",
                "viewCount": 2,
                "viewOffset": 0,
                "duration": 5146058,
            },
            {
                "index": 8,
                "title": "Do You Take One Another?",
                "viewCount": 0,
                "viewOffset": 3910000,
                "duration": 5146058,
            },
            {
                "index": 12,
                "title": "In Sickness And In Health",
                "viewCount": 0,
                "viewOffset": 0,
                "duration": 5146058,
            },
        ]
    }
}


def _mock_watch_status_upstream(
    httpx_mock, *, include_show: bool = True, include_sessions: bool = True
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/accounts",
        json=PLEX_ACCOUNTS_JSON,
    )
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/status/sessions",
        json=PLEX_SESSIONS_JSON if include_sessions else {"MediaContainer": {"Metadata": []}},
    )
    if not include_sessions:
        httpx_mock.add_response(
            method="GET",
            url="http://plex:32400/hubs/home/continueWatching",
            json=PLEX_CONTINUE_WATCHING_JSON,
        )
    if include_show:
        httpx_mock.add_response(
            method="GET",
            url="http://plex:32400/search?query=90+Day+Fianc%C3%A9&limit=25",
            json=PLEX_SEARCH_SHOW_JSON,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://plex:32400/library/metadata/1097/children",
            json=PLEX_SHOW_SEASONS_JSON,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://plex:32400/library/metadata/3745/children?includeUserState=1",
            json=PLEX_SEASON_EPISODES_JSON,
        )


def test_watch_status_active_session_and_show_progress(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="plex-token-123")
    _mock_watch_status_upstream(httpx_mock)
    raw = plex_watch_status("Quinn", show="90 Day Fiancé", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is True
    data = env["data"]
    assert data["code"] == "OK"
    assert data["username"] == "Quinn Cartwright"
    assert data["active_session"]["title"] == "Do You Take One Another?"
    assert data["active_session"]["episode_index"] == 8
    assert data["show_progress"]["show"] == "90 Day Fiancé"
    assert data["show_progress"]["seasons"][0]["watched"] == 1
    assert data["show_progress"]["seasons"][0]["in_progress"] == 1
    episode_titles = [e["title"] for e in data["show_progress"]["seasons"][0]["episodes"]]
    assert "In Sickness And In Health" not in episode_titles
    requests = httpx_mock.get_requests()
    season_req = [r for r in requests if "3745/children" in str(r.url)][0]
    assert season_req.headers.get("X-Plex-User-Id") == "806490180"
    assert season_req.headers.get("X-Plex-Token") == "plex-token-123"


def test_watch_status_unknown_user(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/accounts",
        json=PLEX_ACCOUNTS_JSON,
    )
    raw = plex_watch_status("Nobody", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is False
    assert env["error"]["code"] == "NOT_FOUND"


def test_watch_status_no_active_session_without_show(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/accounts",
        json=PLEX_ACCOUNTS_JSON,
    )
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/status/sessions",
        json={"MediaContainer": {"Metadata": []}},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/hubs/home/continueWatching",
        json=PLEX_CONTINUE_WATCHING_JSON,
    )
    raw = plex_watch_status("Quinn Cartwright", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is True
    assert env["data"]["code"] == "OK"
    assert env["data"]["active_session"] is None
    recent = env["data"]["recent_activity"]
    assert recent["title"] == "For Richer Or For Poorer"
    assert recent["episode_index"] == 11
    assert recent["state"] == "stopped"
    assert recent["watch_status"] == "in_progress"


def test_watch_status_no_activity_when_nothing_recent(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/accounts",
        json=PLEX_ACCOUNTS_JSON,
    )
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/status/sessions",
        json={"MediaContainer": {"Metadata": []}},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://plex:32400/hubs/home/continueWatching",
        json={"MediaContainer": {"Metadata": []}},
    )
    raw = plex_watch_status("Quinn Cartwright", _settings=settings)
    env = json.loads(raw)
    assert env["ok"] is True
    assert env["data"]["code"] == "NO_ACTIVITY"
    assert env["data"]["recent_activity"] is None


def test_watch_status_token_not_leaked(httpx_mock, mcp_settings) -> None:
    settings = replace(mcp_settings, plex_token="super-secret-plex-token-xyz")
    _mock_watch_status_upstream(httpx_mock, include_show=False)
    raw = plex_watch_status("Quinn", _settings=settings)
    assert "super-secret-plex-token-xyz" not in raw

