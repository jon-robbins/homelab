"""MUTATIONS_DISABLED refusal coverage across every mutation module.

With ``enable_mutations=False`` (the fail-closed Settings default and the
``homelab-mcp-telegram`` compose pin), every mutation tool must refuse at
call time with ``MUTATIONS_DISABLED`` regardless of arguments. Complements
``test_tools_media_downloads.py::test_grab_mutations_disabled`` and
``test_mutations_exclusive.py`` so no mutation module can silently lose its
runtime guard.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from homelab_mcp.config import Settings
from homelab_mcp.tools.mutations import autobrr, library, media_files, qbt

CASES: list[tuple[str, Callable[[Settings], str]]] = [
    (
        "qbt_torrents_resume",
        lambda s: qbt.torrents_resume(s, tag="t", hashes=["h"], confirm=True),
    ),
    ("qbt_apply_profile", lambda s: qbt.apply_profile(s, profile="p", expected_action="x")),
    (
        "qbt_torrents_pause",
        lambda s: qbt.torrents_pause(s, tag="t", hashes=["h"], expected_action="x"),
    ),
    ("qbt_offload_run", lambda s: qbt.offload_run(s, limit=1, expected_action="x")),
    (
        "qbt_torrent_force_start",
        lambda s: qbt.qbt_torrent_force_start(s, confirm=True, torrent_hash="h"),
    ),
    (
        "autobrr_filter_set_enabled",
        lambda s: autobrr.filter_set_enabled(s, filter_name="f", enabled=True, confirm=True),
    ),
    ("autobrr_irc_restart", lambda s: autobrr.irc_restart(s, network="n", confirm=True)),
    ("media_file_move", lambda s: media_files.file_move(s, src="/a", dest="/b", expected_action="x")),
    ("media_file_delete", lambda s: media_files.file_delete(s, path="/a", expected_action="x")),
    ("media_trash_empty", lambda s: media_files.trash_empty(s, older_than_days=0, expected_action="x")),
    (
        "qbt_torrents_delete",
        lambda s: library.torrents_delete(s, hashes=["a" * 40], delete_files=True, expected_action="x"),
    ),
    (
        "arr_item_delete",
        lambda s: library.arr_item_delete(
            s, media_type="movie", library_id=1, delete_files=True, expected_action="x"
        ),
    ),
    (
        "seerr_request_action",
        lambda s: library.seerr_request_action(s, request_id=1, action="approve", confirm=True),
    ),
]


@pytest.mark.parametrize("call", [call for _, call in CASES], ids=[name for name, _ in CASES])
def test_mutation_refused_when_disabled(mcp_settings: Settings, call: Callable[[Settings], str]) -> None:
    body = json.loads(call(mcp_settings))
    assert body["ok"] is False
    assert body["error"]["code"] == "MUTATIONS_DISABLED"
