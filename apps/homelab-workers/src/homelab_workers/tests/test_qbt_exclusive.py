from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from homelab_workers.shared import qbt_exclusive as ex


def _t(hash_: str, name: str, *, state="downloading", progress=0.0):
    return {"hash": hash_, "name": name, "state": state, "progress": progress}


def test_match_name_contains_case_insensitive():
    rows = [_t("a", "90 Day Fiance S11E01"), _t("b", "Other Show")]
    matched = ex.match_name_contains(rows, "90 day fiance")
    assert [r["hash"] for r in matched] == ["a"]


def test_match_name_contains_ignores_dot_underscore_hyphen_spacing():
    rows = [
        _t("a", "90.Day.Fiance.S11E14.1080p.WEB.h264-FREQUENCY"),
        _t("b", "90_Day_Fiance_S11E15"),
        _t("c", "Other-Show"),
    ]
    matched = ex.match_name_contains(rows, "90 Day Fiance")
    assert [r["hash"] for r in matched] == ["a", "b"]


def test_start_pauses_competitors_and_force_starts_matches(tmp_path: Path):
    qbt = MagicMock()
    qbt.torrents_info_raw.return_value = [
        _t("p1", "90 Day Fiance S11E01"),
        _t("p2", "90 Day Fiance S11E02", progress=1.0, state="uploading"),
        _t("x1", "Halo Remux", state="downloading"),
        _t("x2", "Stopped Movie", state="stoppedDL"),
    ]
    path = str(tmp_path / "session.json")
    out = ex.start_exclusive(qbt, name_contains="90 Day Fiance", session_path=path)
    assert out["priority_count"] == 2
    assert set(out["priority_hashes"]) == {"p1", "p2"}
    assert out["paused_hashes"] == ["x1"]  # only active non-match
    qbt.torrents_pause.assert_called_once()
    assert set(qbt.torrents_pause.call_args.kwargs["hashes"]) == {"x1"}
    qbt.torrents_set_force_start.assert_called()
    qbt.torrents_set_priority.assert_called()
    # p2 already done → in completed
    session = ex.read_session(path)
    assert "p2" in session.completed_hashes
    assert "p1" in session.priority_hashes


def test_start_raises_when_session_active(tmp_path: Path):
    qbt = MagicMock()
    qbt.torrents_info_raw.return_value = [_t("p1", "Show A")]
    path = str(tmp_path / "session.json")
    ex.start_exclusive(qbt, name_contains="Show A", session_path=path)
    with pytest.raises(ex.SessionActiveError):
        ex.start_exclusive(qbt, name_contains="Show A", session_path=path)


def test_mark_finished_resumes_when_all_done(tmp_path: Path):
    qbt = MagicMock()
    path = str(tmp_path / "session.json")
    qbt.torrents_info_raw.return_value = [
        _t("p1", "Show A E01"),
        _t("x1", "Other", state="downloading"),
    ]
    ex.start_exclusive(qbt, name_contains="Show A", session_path=path)
    qbt.reset_mock()
    qbt.torrents_info_raw.return_value = [
        _t("p1", "Show A E01", progress=1.0, state="uploading"),
    ]
    detail = ex.mark_finished(qbt, hash_value="p1", session_path=path)
    assert detail["handled"] is True
    assert detail["action"] == "exclusive_session_complete"
    qbt.torrents_resume.assert_called_once()
    assert ex.read_session(path) is None


def test_mark_finished_noop_wrong_hash(tmp_path: Path):
    qbt = MagicMock()
    path = str(tmp_path / "session.json")
    qbt.torrents_info_raw.return_value = [_t("p1", "Show A")]
    ex.start_exclusive(qbt, name_contains="Show A", session_path=path)
    detail = ex.mark_finished(qbt, hash_value="deadbeef", session_path=path)
    assert detail["handled"] is False
    assert detail["action"] == "noop"


def test_removed_priority_hash_counts_as_done(tmp_path: Path):
    qbt = MagicMock()
    path = str(tmp_path / "session.json")
    qbt.torrents_info_raw.return_value = [
        _t("p1", "Show A E01"),
        _t("p2", "Show A E02"),
    ]
    ex.start_exclusive(qbt, name_contains="Show A", session_path=path)
    qbt.torrents_info_raw.return_value = []  # both gone
    detail = ex.mark_finished(qbt, hash_value="p1", session_path=path)
    assert detail["handled"] is True
    assert ex.read_session(path) is None


def test_reenforce_stops_new_competitor_and_adds_new_match(tmp_path: Path):
    qbt = MagicMock()
    path = str(tmp_path / "session.json")
    qbt.torrents_info_raw.return_value = [_t("p1", "Show A E01")]
    ex.start_exclusive(qbt, name_contains="Show A", session_path=path)
    qbt.reset_mock()
    qbt.torrents_info_raw.return_value = [
        _t("p1", "Show A E01"),
        _t("p3", "Show A E03"),  # new match
        _t("y1", "New Remux", state="downloading"),
    ]
    ex.reenforce_exclusive(qbt, session_path=path)
    session = ex.read_session(path)
    assert "p3" in session.priority_hashes
    assert "y1" in session.paused_hashes
    qbt.torrents_pause.assert_called()
    # Re-boost includes existing + newly matched remaining hashes
    boosted = set(qbt.torrents_set_force_start.call_args.args[0])
    assert boosted == {"p1", "p3"}


def test_boost_sets_force_start_after_resume():
    qbt = MagicMock()
    ex._boost_priority_hashes(qbt, ["abc"])
    assert [c[0] for c in qbt.method_calls] == [
        "torrents_set_priority",
        "torrents_resume",
        "torrents_set_force_start",
    ]
    qbt.torrents_set_force_start.assert_called_once_with(["abc"], True)


def test_reenforce_reboosts_existing_priority_when_force_dropped(tmp_path: Path):
    qbt = MagicMock()
    path = str(tmp_path / "session.json")
    qbt.torrents_info_raw.return_value = [_t("p1", "Show A E01")]
    ex.start_exclusive(qbt, name_contains="Show A", session_path=path)
    qbt.reset_mock()
    qbt.torrents_info_raw.return_value = [
        _t("p1", "Show A E01", state="queuedDL"),
    ]
    ex.reenforce_exclusive(qbt, session_path=path)
    qbt.torrents_set_force_start.assert_called_once_with(["p1"], True)
    qbt.torrents_set_priority.assert_called_once_with(["p1"], "top")


def test_cancel_resumes_paused(tmp_path: Path):
    qbt = MagicMock()
    path = str(tmp_path / "session.json")
    qbt.torrents_info_raw.return_value = [
        _t("p1", "Show A"),
        _t("x1", "Other", state="downloading"),
    ]
    ex.start_exclusive(qbt, name_contains="Show A", session_path=path)
    qbt.reset_mock()
    out = ex.cancel_exclusive(qbt, session_path=path)
    assert out["resumed_count"] == 1
    qbt.torrents_resume.assert_called_once()
    assert ex.read_session(path) is None
