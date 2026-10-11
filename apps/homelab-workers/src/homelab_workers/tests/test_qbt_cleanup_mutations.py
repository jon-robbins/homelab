from urllib.parse import parse_qs

from homelab_workers.shared.qbittorrent import QBittorrentClient


def login(httpx_mock) -> None:
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")


def test_metadata_helpers(httpx_mock) -> None:
    login(httpx_mock)
    httpx_mock.add_response(
        method="GET",
        url="http://qbt:8080/api/v2/torrents/properties?hash=abc",
        json={"save_path": "/downloads/seed-staging"},
    )
    httpx_mock.add_response(
        method="GET",
        url="http://qbt:8080/api/v2/torrents/files?hash=abc",
        json=[{"name": "file.mkv"}],
    )
    httpx_mock.add_response(
        method="GET",
        url="http://qbt:8080/api/v2/torrents/export?hash=abc",
        content=b"torrent-bytes",
    )
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        assert client.torrent_properties("abc")["save_path"] == "/downloads/seed-staging"
        assert client.torrent_files("abc")[0]["name"] == "file.mkv"
        assert client.torrent_export("abc") == b"torrent-bytes"


def test_delete_files_false_is_sent(httpx_mock) -> None:
    login(httpx_mock)
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(
        method="POST",
        url="http://qbt:8080/api/v2/torrents/delete",
        text="Ok.",
    )
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        client.torrents_delete_hashes(["abc", "def"], delete_files=False)
    body = parse_qs(httpx_mock.get_requests()[-1].content.decode())
    assert body["hashes"] == ["abc|def"]
    assert body["deleteFiles"] == ["false"]


def test_force_start_and_priority(httpx_mock) -> None:
    login(httpx_mock)
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(
        method="POST",
        url="http://qbt:8080/api/v2/torrents/setForceStart",
        text="Ok.",
    )
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(
        method="POST",
        url="http://qbt:8080/api/v2/torrents/topPrio",
        text="Ok.",
    )
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        assert client.torrents_set_force_start(["abc"], False) == 1
        assert client.torrents_set_priority(["abc"], "top") == 1


def test_sequential_download_and_first_last_piece_priority_toggles(httpx_mock) -> None:
    login(httpx_mock)
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(
        method="POST",
        url="http://qbt:8080/api/v2/torrents/toggleSequentialDownload",
        text="Ok.",
    )
    httpx_mock.add_response(method="POST", url="http://qbt:8080/api/v2/auth/login", text="Ok.")
    httpx_mock.add_response(
        method="POST",
        url="http://qbt:8080/api/v2/torrents/toggleFirstLastPiecePrio",
        text="Ok.",
    )
    with QBittorrentClient("http://qbt:8080", "u", "p") as client:
        assert client.torrents_toggle_sequential_download(["abc"]) == 1
        assert client.torrents_toggle_first_last_piece_priority(["abc"]) == 1
    requests = httpx_mock.get_requests()
    sequential_body = parse_qs(requests[-3].content.decode())
    first_last_body = parse_qs(requests[-1].content.decode())
    assert sequential_body["hashes"] == ["abc"]
    assert first_last_body["hashes"] == ["abc"]
