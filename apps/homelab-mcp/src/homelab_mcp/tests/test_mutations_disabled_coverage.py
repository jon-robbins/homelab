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
from homelab_mcp.tools.mutations import qbt

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
]


@pytest.mark.parametrize("call", [call for _, call in CASES], ids=[name for name, _ in CASES])
def test_mutation_refused_when_disabled(mcp_settings: Settings, call: Callable[[Settings], str]) -> None:
    body = json.loads(call(mcp_settings))
    assert body["ok"] is False
    assert body["error"]["code"] == "MUTATIONS_DISABLED"
