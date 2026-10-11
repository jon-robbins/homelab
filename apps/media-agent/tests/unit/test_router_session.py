"""RouterStateStore + canonical_option_id + build_pending_options."""

from __future__ import annotations

import time

from app.models.router import RouterPendingOption, RouterSessionState
from app.router.intent import build_pending_options, canonical_option_id
from app.router.session import RouterStateStore


def test_canonical_option_id_is_deterministic() -> None:
    a = canonical_option_id(
        source_action="indexer_search",
        rank=1,
        title="Crazy Ex-Girlfriend S04 Pack",
        guid="guid-1",
        episode_id=None,
        movie_id=None,
        release={"guid": "rel-guid", "infoHash": "abc123"},
    )
    b = canonical_option_id(
        source_action="indexer_search",
        rank=1,
        title="Crazy Ex-Girlfriend S04 Pack",
        guid="guid-1",
        episode_id=None,
        movie_id=None,
        release={"guid": "rel-guid", "infoHash": "abc123"},
    )
    assert a == b
    assert a.startswith("opt-01-")


def test_build_pending_options_carries_release_only_for_indexer_search() -> None:
    result = {
        "ok": True,
        "options": [
            {
                "rank": 1,
                "title": "X",
                "release": {"guid": "g1", "indexerId": 2},
            }
        ],
    }
    pending = build_pending_options("indexer_search", result)
    assert len(pending) == 1
    assert pending[0].release == {"guid": "g1", "indexerId": 2}

    pending_tv = build_pending_options("download_options_tv", result)
    assert pending_tv[0].release is None


def test_router_state_store_set_get_clear(tmp_path) -> None:
    store = RouterStateStore(str(tmp_path / "state.json"))
    state = RouterSessionState(
        session_key="k1",
        created_at_ms=int(time.time() * 1000),
        expires_at_ms=int(time.time() * 1000) + 60_000,
        source_action="indexer_search",
        query="q",
        media_type="tv",
        season=4,
        options=[RouterPendingOption(rank=1, title="t", guid="g")],
    )
    store.set(state)
    fetched = store.get("k1")
    assert fetched is not None
    assert fetched.session_key == "k1"
    assert fetched.options[0].title == "t"
    store.clear("k1")
    assert store.get("k1") is None


def test_router_state_store_expires_state(tmp_path) -> None:
    store = RouterStateStore(str(tmp_path / "state.json"))
    state = RouterSessionState(
        session_key="k1",
        created_at_ms=1,
        expires_at_ms=2,
        source_action="indexer_search",
        query="q",
    )
    store.set(state)
    assert store.get("k1") is None
