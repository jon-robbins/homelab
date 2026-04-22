from __future__ import annotations

import pytest

from homelab_workers.arr_retry.qbittorrent import QBittorrentClient


def test_qbittorrent_client_login_success_creates_client(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://qbit:8080/api/v2/auth/login",
        text="Ok.",
    )

    client = QBittorrentClient("http://qbit:8080", "admin", "pass")
    assert client.base_url == "http://qbit:8080"
    client.close()


def test_qbittorrent_client_login_failure_raises_runtime_error(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://qbit:8080/api/v2/auth/login",
        text="Fails.",
    )

    with pytest.raises(RuntimeError, match="login failed"):
        QBittorrentClient("http://qbit:8080", "admin", "wrong")


def test_qbittorrent_torrents_info_maps_payload(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://qbit:8080/api/v2/auth/login",
        text="Ok.",
    )
    httpx_mock.add_response(
        method="GET",
        url="http://qbit:8080/api/v2/torrents/info",
        json=[
            {
                "hash": "ABCD1234",
                "name": "Ubuntu ISO",
                "category": "linux",
                "added_on": 123,
                "progress": 0.4,
                "dlspeed": 50.0,
                "downloaded": 2000,
                "time_active": 10,
                "state": "downloading",
                "num_seeds": 20,
                "amount_left": 1000,
            }
        ],
    )

    with QBittorrentClient("http://qbit:8080", "admin", "pass") as client:
        torrents = client.torrents_info()

    assert len(torrents) == 1
    torrent = torrents[0]
    assert torrent.torrent_hash == "abcd1234"
    assert torrent.average_download_bps == 200.0
    assert torrent.is_complete is False


def test_qbittorrent_torrents_info_non_list_payload_returns_empty(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://qbit:8080/api/v2/auth/login",
        text="Ok.",
    )
    httpx_mock.add_response(
        method="GET",
        url="http://qbit:8080/api/v2/torrents/info",
        json={"not": "a-list"},
    )

    with QBittorrentClient("http://qbit:8080", "admin", "pass") as client:
        torrents = client.torrents_info()

    assert torrents == []


def test_qbittorrent_torrents_delete_hashes_reauths_and_posts_delete(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://qbit:8080/api/v2/auth/login",
        text="Ok.",
    )
    httpx_mock.add_response(
        method="POST",
        url="http://qbit:8080/api/v2/auth/login",
        text="Ok.",
    )
    httpx_mock.add_response(
        method="POST",
        url="http://qbit:8080/api/v2/torrents/delete",
        status_code=200,
    )

    with QBittorrentClient("http://qbit:8080", "admin", "pass") as client:
        client.torrents_delete_hashes(["hash1", "hash2"], delete_files=True)

    requests = httpx_mock.get_requests()
    assert requests[-1].url.path == "/api/v2/torrents/delete"
    body = requests[-1].content.decode("utf-8")
    assert "hashes=hash1%7Chash2" in body
    assert "deleteFiles=true" in body


def test_qbittorrent_context_manager_closes_client(httpx_mock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="http://qbit:8080/api/v2/auth/login",
        text="Ok.",
    )

    with QBittorrentClient("http://qbit:8080", "admin", "pass") as client:
        assert client._client.is_closed is False

    assert client._client.is_closed is True
