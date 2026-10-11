from urllib.parse import parse_qs

from homelab_workers.shared.qbittorrent import QBittorrentClient


def test_torrents_resume_by_tag(httpx_mock) -> None:
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(
        method="GET",
        url="http://qbt:8080/api/v2/torrents/info?tag=freeleech",
        json=[{"hash": "abc", "state": "pausedDL"}],
    )
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/torrents/start", text="Ok.")
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        count = client.torrents_resume(tag="freeleech")
    assert count == 1


def test_torrents_pause_by_hashes(httpx_mock) -> None:
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/torrents/stop", text="Ok.")
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        count = client.torrents_pause(hashes=["abc123", "def456"])
    assert count == 2


def test_set_app_preferences(httpx_mock) -> None:
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/app/setPreferences", text="Ok.")
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        client.set_app_preferences({"max_active_downloads": 20})
    req = httpx_mock.get_requests()[-1]
    assert req.method == "POST"
    body = parse_qs(req.content.decode())
    assert body["json"] == ['{"max_active_downloads": 20}']


def test_torrents_set_location(httpx_mock) -> None:
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/torrents/setLocation", text="Ok.")
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        client.torrents_set_location(["abc"], "/seeding/Show")


def test_torrents_set_download_limit(httpx_mock) -> None:
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(
        method="POST", url="http://qbt:8080/api/v2/torrents/setDownloadLimit", text="Ok."
    )
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        count = client.torrents_set_download_limit(["abc", "def"], 10240)
    assert count == 2
    req = httpx_mock.get_requests()[-1]
    body = parse_qs(req.content.decode())
    assert body["hashes"] == ["abc|def"]
    assert body["limit"] == ["10240"]
    assert req.url.path.endswith("/api/v2/torrents/setDownloadLimit")


def test_torrents_set_upload_limit_noop_empty(httpx_mock) -> None:
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        assert client.torrents_set_upload_limit([], 0) == 0


def test_torrents_set_category(httpx_mock) -> None:
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/torrents/setCategory")
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        assert client.torrents_set_category(["abc"], "seed-group") == 1
    req = httpx_mock.get_requests()[-1]
    body = parse_qs(req.content.decode())
    assert body["hashes"] == ["abc"]
    assert body["category"] == ["seed-group"]
    assert req.url.path.endswith("/api/v2/torrents/setCategory")
