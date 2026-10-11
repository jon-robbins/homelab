from homelab_workers.shared.tmdb import TmdbClient


def test_search_movie_parses_first_result(httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://api.themoviedb.org/3/search/movie?api_key=test-key&query=Shelter&year=2026",
        json={
            "results": [
                {"id": 42, "title": "Shelter", "release_date": "2026-03-15"},
                {"id": 99, "title": "Other", "release_date": "2020-01-01"},
            ]
        },
    )
    client = TmdbClient("test-key")
    movie = client.search_movie("Shelter", year=2026)
    assert movie is not None
    assert movie.id == 42
    assert movie.title == "Shelter"
    assert movie.year == 2026


def test_search_movie_returns_none_when_empty(httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://api.themoviedb.org/3/search/movie?api_key=test-key&query=Unknown",
        json={"results": []},
    )
    client = TmdbClient("test-key")
    assert client.search_movie("Unknown") is None


def test_search_tv_episode_parses_show_and_episode(httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://api.themoviedb.org/3/search/tv?api_key=test-key&query=Some+Show",
        json={"results": [{"id": 7, "name": "Some Show"}]},
    )
    httpx_mock.add_response(
        url="https://api.themoviedb.org/3/tv/7/season/1/episode/2?api_key=test-key",
        json={"name": "Pilot", "season_number": 1, "episode_number": 2},
    )
    client = TmdbClient("test-key")
    episode = client.search_tv_episode("Some Show", season=1, episode=2)
    assert episode is not None
    assert episode.show_name == "Some Show"
    assert episode.season == 1
    assert episode.episode == 2
    assert episode.episode_title == "Pilot"


def test_search_tv_episode_without_episode_title_on_404(httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://api.themoviedb.org/3/search/tv?api_key=test-key&query=Some+Show",
        json={"results": [{"id": 7, "name": "Some Show"}]},
    )
    httpx_mock.add_response(
        url="https://api.themoviedb.org/3/tv/7/season/1/episode/99?api_key=test-key",
        status_code=404,
    )
    client = TmdbClient("test-key")
    episode = client.search_tv_episode("Some Show", season=1, episode=99)
    assert episode is not None
    assert episode.show_name == "Some Show"
    assert episode.episode_title is None
