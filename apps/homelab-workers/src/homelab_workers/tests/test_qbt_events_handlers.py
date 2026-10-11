from pathlib import Path
from unittest.mock import MagicMock

from homelab_workers.shared import qbt_exclusive as ex
from homelab_workers.shared.qbt_events_handlers import dispatch_torrent_finished


def test_dispatch_idle_session_noop(tmp_path: Path):
    qbt = MagicMock()
    out = dispatch_torrent_finished(
        qbt, hash_value="abc", session_path=str(tmp_path / "s.json")
    )
    assert out["ok"] is True
    assert out["handlers"][0]["name"] == "exclusive"
    assert out["handlers"][0]["handled"] is False


def test_dispatch_marks_priority_hash(tmp_path: Path):
    qbt = MagicMock()
    path = str(tmp_path / "s.json")
    qbt.torrents_info_raw.return_value = [
        {"hash": "p1", "name": "Show A", "state": "downloading", "progress": 0.0},
    ]
    ex.start_exclusive(qbt, name_contains="Show A", session_path=path)
    qbt.torrents_info_raw.return_value = [
        {"hash": "p1", "name": "Show A", "state": "uploading", "progress": 1.0},
    ]
    out = dispatch_torrent_finished(qbt, hash_value="p1", session_path=path)
    assert out["handlers"][0]["handled"] is True
