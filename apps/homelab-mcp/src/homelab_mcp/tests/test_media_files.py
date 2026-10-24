from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from homelab_mcp.file_paths import PathRejected, resolve_in_roots
from homelab_mcp.tools import media_files
from homelab_mcp.tools.mutations import media_files as mut_files


@pytest.fixture
def roots(tmp_path: Path) -> dict[str, Path]:
    made = {name: tmp_path / name for name in ("downloads", "movies", "tv")}
    for path in made.values():
        path.mkdir()
    return made


@pytest.fixture
def file_settings(mcp_settings, roots):
    return replace(
        mcp_settings,
        enable_mutations=True,
        media_file_roots=tuple(str(p) for p in roots.values()),
        audit_path=str(roots["downloads"].parent / "audit.jsonl"),
    )


def _body(raw: str) -> dict:
    return json.loads(raw)


def test_resolve_rejects_paths_outside_roots(roots) -> None:
    allowed = tuple(str(p) for p in roots.values())
    with pytest.raises(PathRejected):
        resolve_in_roots("/etc/passwd", allowed)
    with pytest.raises(PathRejected):
        resolve_in_roots(f"{roots['movies']}/../../escape", allowed)
    with pytest.raises(PathRejected):
        resolve_in_roots("relative/path", allowed)


def test_resolve_rejects_root_itself_and_trash(roots) -> None:
    allowed = tuple(str(p) for p in roots.values())
    with pytest.raises(PathRejected):
        resolve_in_roots(str(roots["movies"]), allowed)
    with pytest.raises(PathRejected):
        resolve_in_roots(f"{roots['movies']}/.homelab-trash/x", allowed)


def test_resolve_rejects_symlink_escape(roots, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (roots["movies"] / "link").symlink_to(outside)
    allowed = tuple(str(p) for p in roots.values())
    with pytest.raises(PathRejected):
        resolve_in_roots(f"{roots['movies']}/link/file", allowed)


def test_resolve_keeps_symlink_leaf_unfollowed(roots, tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (roots["movies"] / "leaf").symlink_to(target)
    allowed = tuple(str(p) for p in roots.values())
    resolved = resolve_in_roots(f"{roots['movies']}/leaf", allowed)
    assert resolved.path == roots["movies"].resolve() / "leaf"


def test_files_list_reports_entries(file_settings, roots) -> None:
    movie = roots["movies"] / "Film (2020)"
    movie.mkdir()
    (movie / "film.mkv").write_bytes(b"x" * 10)
    body = _body(media_files.files_list(file_settings, path=str(movie)))
    assert body["ok"] is True
    assert body["data"]["entries"] == [
        {"name": "film.mkv", "type": "file", "size_bytes": 10, "mtime": body["data"]["entries"][0]["mtime"]}
    ]


def test_files_list_without_path_lists_roots(file_settings, roots) -> None:
    body = _body(media_files.files_list(file_settings, path=None))
    assert body["ok"] is True
    assert sorted(e["name"] for e in body["data"]["entries"]) == sorted(
        str(p.resolve()) for p in roots.values()
    )


def test_files_list_rejects_outside_path(file_settings) -> None:
    body = _body(media_files.files_list(file_settings, path="/etc"))
    assert body["error"]["code"] == "PATH_REJECTED"


def test_move_requires_expected_action(file_settings, roots) -> None:
    src = roots["downloads"] / "Film.mkv"
    src.write_bytes(b"x")
    dest = roots["movies"] / "Film (2020)" / "Film.mkv"
    body = _body(mut_files.file_move(file_settings, src=str(src), dest=str(dest)))
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"
    assert src.exists()


def test_move_applies_and_creates_parent(file_settings, roots) -> None:
    src = roots["downloads"] / "Film.mkv"
    src.write_bytes(b"x")
    dest = roots["movies"] / "Film (2020)" / "Film.mkv"
    expected = f"file-move:{src.resolve()}->{dest.parent.resolve()}/Film.mkv"
    body = _body(
        mut_files.file_move(file_settings, src=str(src), dest=str(dest), expected_action=expected)
    )
    assert body["ok"] is True, body
    assert dest.read_bytes() == b"x"
    assert not src.exists()


def test_move_refuses_overwrite(file_settings, roots) -> None:
    src = roots["downloads"] / "a.mkv"
    src.write_bytes(b"new")
    dest = roots["movies"] / "a.mkv"
    dest.write_bytes(b"old")
    expected = f"file-move:{src.resolve()}->{dest.resolve()}"
    body = _body(
        mut_files.file_move(file_settings, src=str(src), dest=str(dest), expected_action=expected)
    )
    assert body["error"]["code"] == "DESTINATION_EXISTS"
    assert dest.read_bytes() == b"old"


def test_move_disabled_when_mutations_off(file_settings, roots) -> None:
    settings = replace(file_settings, enable_mutations=False)
    body = _body(mut_files.file_move(settings, src="/x", dest="/y"))
    assert body["error"]["code"] == "MUTATIONS_DISABLED"


def test_delete_moves_into_trash_and_audits(file_settings, roots) -> None:
    target = roots["tv"] / "Show" / "Season 01"
    target.mkdir(parents=True)
    (target / "ep.mkv").write_bytes(b"x")
    expected = f"file-delete:{target.resolve()}"
    body = _body(
        mut_files.file_delete(file_settings, path=str(target), expected_action=expected)
    )
    assert body["ok"] is True, body
    assert not target.exists()
    trashed = Path(body["data"]["applied"]["trash_path"])
    assert trashed.parent == roots["tv"].resolve() / ".homelab-trash"
    assert (trashed / "ep.mkv").exists()
    audit = Path(file_settings.audit_path).read_text().splitlines()
    assert json.loads(audit[-1])["tool"] == "media_file_delete"


def test_delete_missing_path(file_settings, roots) -> None:
    missing = roots["tv"] / "nope"
    body = _body(
        mut_files.file_delete(
            file_settings, path=str(missing), expected_action=f"file-delete:{missing.resolve()}"
        )
    )
    assert body["error"]["code"] == "NOT_FOUND"


def test_trash_list_and_empty(file_settings, roots) -> None:
    trash = roots["movies"] / ".homelab-trash"
    trash.mkdir()
    old = trash / "old-entry"
    old.mkdir()
    (old / "f.mkv").write_bytes(b"x" * 5)
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))
    fresh = trash / "fresh-entry.mkv"
    fresh.write_bytes(b"y")

    listed = _body(media_files.trash_list(file_settings))
    assert {e["name"] for e in listed["data"]["entries"]} == {old.name, fresh.name}

    body = _body(
        mut_files.trash_empty(file_settings, older_than_days=7, expected_action="trash-empty:7")
    )
    assert body["ok"] is True, body
    assert body["data"]["applied"]["removed_count"] == 1
    assert not old.exists()
    assert fresh.exists()


def test_files_list_accepts_a_root(file_settings, roots) -> None:
    (roots["downloads"] / "Film.mkv").write_bytes(b"x")
    (roots["downloads"] / ".homelab-trash").mkdir()
    body = _body(media_files.files_list(file_settings, path=str(roots["downloads"])))
    assert body["ok"] is True, body
    assert [e["name"] for e in body["data"]["entries"]] == ["Film.mkv"]
