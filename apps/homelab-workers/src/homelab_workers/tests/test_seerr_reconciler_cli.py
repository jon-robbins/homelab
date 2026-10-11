import json
from types import SimpleNamespace

from homelab_workers.cli import seerr_reconciler
from homelab_workers.shared.seerr_reconciler_models import NormalizedSeerrRequest, StableIds


def _config(tmp_path, *, enabled=True, fallback_enabled=False):
    return seerr_reconciler.ReconcilerConfig(
        enabled=enabled,
        fallback_enabled=fallback_enabled,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(tmp_path / "state.json"),
        status_path=str(tmp_path / "status.json"),
        status_max_age_seconds=900,
    )


def _series_request() -> NormalizedSeerrRequest:
    return NormalizedSeerrRequest(
        request_id=77,
        media_type="series",
        title="Stag",
        year=2024,
        seasons=(1,),
        stable_ids=StableIds(tmdb_id=100, tvdb_id=200),
        arr_id=243,
        status="approved",
        requested_at="2026-08-16T11:59:59Z",
    )


def _movie_request() -> NormalizedSeerrRequest:
    return NormalizedSeerrRequest(
        request_id=88,
        media_type="movie",
        title="Her Story",
        year=2026,
        seasons=(),
        stable_ids=StableIds(tmdb_id=300, imdb_id="tt123"),
        arr_id=42,
        status="approved",
        requested_at="2026-08-16T11:59:59Z",
    )


def test_parse_args_supports_dry_run_once_healthcheck():
    args = seerr_reconciler.parse_args(["--dry-run", "--once"])
    assert args.dry_run is True
    assert args.once is True

    health = seerr_reconciler.parse_args(["--healthcheck"])
    assert health.healthcheck is True


def test_main_bootstraps_container_secrets_before_healthcheck(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        seerr_reconciler,
        "bootstrap_container_secrets",
        lambda: calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        seerr_reconciler,
        "parse_args",
        lambda argv=None: SimpleNamespace(healthcheck=True),
    )
    monkeypatch.setattr(seerr_reconciler, "load_reconciler_config", lambda: object())
    monkeypatch.setattr(seerr_reconciler, "healthcheck", lambda _config: 0)

    assert seerr_reconciler.main(["--healthcheck"]) == 0
    assert calls == ["bootstrap"]


def test_env_defaults_are_disabled(monkeypatch):
    monkeypatch.delenv("SEERR_RECONCILER_ENABLED", raising=False)
    monkeypatch.delenv("SEERR_RECONCILER_FALLBACK_ENABLED", raising=False)

    config = seerr_reconciler.load_reconciler_config()

    assert config.enabled is False
    assert config.fallback_enabled is False
    assert config.lookback_hours == 24


def test_arr_releases_for_series_uses_sonarr_release_search(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_search(client, **kwargs):
        calls["client"] = client
        calls.update(kwargs)
        return {
            "ok": True,
            "options": [
                {
                    "title": "Stag S01 1080p",
                    "guid": "arr-guid",
                    "download_allowed": False,
                    "rejections": ["Release is over maximum size"],
                }
            ],
        }

    monkeypatch.setattr(seerr_reconciler, "search_sonarr_releases", fake_search)

    client = object()
    rows = seerr_reconciler._arr_releases_for_request(client, _series_request(), _config(tmp_path))

    assert rows == [
        {
            "title": "Stag S01 1080p",
            "guid": "arr-guid",
            "download_allowed": False,
            "rejections": ["Release is over maximum size"],
        }
    ]
    assert calls["client"] is client
    assert calls["query"] == "Stag"
    assert calls["season"] == 1
    assert calls["series_id"] == 243
    assert calls["include_full_series_packs"] is True


def test_arr_releases_for_movie_uses_radarr_release_search(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_search(client, **kwargs):
        calls["client"] = client
        calls.update(kwargs)
        return {
            "ok": True,
            "options": [
                {
                    "title": "Her Story 2026 1080p",
                    "guid": "movie-guid",
                    "download_allowed": False,
                    "rejections": ["Cutoff unmet"],
                }
            ],
        }

    monkeypatch.setattr(seerr_reconciler, "search_radarr_releases", fake_search)

    client = object()
    rows = seerr_reconciler._arr_releases_for_request(client, _movie_request(), _config(tmp_path))

    assert rows == [
        {
            "title": "Her Story 2026 1080p",
            "guid": "movie-guid",
            "download_allowed": False,
            "rejections": ["Cutoff unmet"],
        }
    ]
    assert calls["client"] is client
    assert calls["query"] == "Her Story"
    assert calls["movie_id"] == 42


def test_arr_releases_for_request_returns_empty_on_search_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        seerr_reconciler,
        "search_sonarr_releases",
        lambda *_args, **_kwargs: {"ok": False, "error": {"code": "NO_RELEASES"}},
    )

    assert seerr_reconciler._arr_releases_for_request(
        object(), _series_request(), _config(tmp_path)
    ) == []


def test_apply_fallback_grabs_skips_when_disabled_or_dry_run(monkeypatch, tmp_path):
    calls: list[object] = []
    monkeypatch.setattr(
        seerr_reconciler,
        "grab_prowlarr_release",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    row = {
        "state_key": "77:series:1",
        "state": "fallback_candidate_found",
        "recommended_action": "grab_fallback",
        "expected_action": "grab:prowlarr:7:prow-guid",
        "candidates": [
            {"guid": "prow-guid", "indexer_id": 7, "title": "Stag S01", "size_bytes": 123}
        ],
    }

    assert seerr_reconciler._apply_fallback_grabs(
        _config(tmp_path, fallback_enabled=False), {"requests": {}}, [row], object(), dry_run=False
    ) == 0
    assert seerr_reconciler._apply_fallback_grabs(
        _config(tmp_path, fallback_enabled=True), {"requests": {}}, [row], object(), dry_run=True
    ) == 0
    assert calls == []


def test_apply_fallback_grabs_records_success(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(seerr_reconciler.time, "time", lambda: 1786881600)

    def fake_grab(client, **kwargs):
        calls.append({"client": client, **kwargs})
        return {"ok": True, "source": "prowlarr"}

    monkeypatch.setattr(seerr_reconciler, "grab_prowlarr_release", fake_grab)
    state = {"requests": {}}
    row = {
        "state_key": "77:series:1",
        "state": "fallback_candidate_found",
        "recommended_action": "grab_fallback",
        "expected_action": "grab:prowlarr:7:prow-guid",
        "candidates": [
            {"guid": "prow-guid", "indexer_id": 7, "title": "Stag S01", "size_bytes": 123}
        ],
    }
    prowlarr = object()

    mutated = seerr_reconciler._apply_fallback_grabs(
        _config(tmp_path, fallback_enabled=True), state, [row], prowlarr, dry_run=False
    )

    assert mutated == 1
    assert calls == [
        {"client": prowlarr, "guid": "prow-guid", "indexer_id": 7, "title": "Stag S01"}
    ]
    assert row["state"] == "fallback_grabbed"
    assert row["reason"] == "Prowlarr fallback grab accepted"
    assert state["requests"]["77:series:1"]["attempts"] == [
        {"guid": "prow-guid", "ok": True, "at": 1786881600}
    ]


def test_apply_fallback_grabs_records_failed_guid(monkeypatch, tmp_path):
    monkeypatch.setattr(seerr_reconciler.time, "time", lambda: 1786881600)
    monkeypatch.setattr(
        seerr_reconciler,
        "grab_prowlarr_release",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error": {"code": "RELEASE_NOT_CACHED", "message": "gone"},
        },
    )
    state = {"requests": {}}
    row = {
        "state_key": "77:series:1",
        "state": "fallback_candidate_found",
        "recommended_action": "grab_fallback",
        "expected_action": "grab:prowlarr:7:prow-guid",
        "candidates": [{"guid": "prow-guid", "indexer_id": 7, "title": "Stag S01"}],
    }

    assert seerr_reconciler._apply_fallback_grabs(
        _config(tmp_path, fallback_enabled=True), state, [row], object(), dry_run=False
    ) == 0
    assert row["state"] == "blocked_no_candidate"
    assert row["reason"] == "gone"
    assert state["requests"]["77:series:1"]["failed_guids"] == ["prow-guid"]


def test_apply_fallback_grabs_skips_recent_attempt(monkeypatch, tmp_path):
    calls: list[object] = []
    monkeypatch.setattr(seerr_reconciler.time, "time", lambda: 1786881600)
    monkeypatch.setattr(
        seerr_reconciler,
        "grab_prowlarr_release",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    state = {
        "requests": {
            "77:series:1": {
                "attempts": [{"guid": "prow-guid", "ok": True, "at": 1786881540}]
            }
        }
    }
    row = {
        "state_key": "77:series:1",
        "state": "fallback_candidate_found",
        "recommended_action": "grab_fallback",
        "expected_action": "grab:prowlarr:7:prow-guid",
        "candidates": [{"guid": "prow-guid", "indexer_id": 7, "title": "Stag S01"}],
    }

    assert seerr_reconciler._apply_fallback_grabs(
        _config(tmp_path, fallback_enabled=True), state, [row], object(), dry_run=False
    ) == 0
    assert calls == []
    assert row["state"] == "fallback_candidate_found"


def test_run_once_dry_run_writes_diagnoses_and_status(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    status_path = tmp_path / "status.json"
    config = seerr_reconciler.ReconcilerConfig(
        enabled=False,
        fallback_enabled=False,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(state_path),
        status_path=str(status_path),
        status_max_age_seconds=900,
    )
    diagnoses = [
        {
            "state_key": "77:series:1",
            "request_id": 77,
            "title": "Stag",
            "media_type": "series",
            "seasons": [1],
            "stable_ids": {"tmdb_id": 100, "tvdb_id": 200, "imdb_id": None},
            "arr_id": 243,
            "state": "blocked_no_candidate",
            "reason": "no candidate",
            "recommended_action": "manual_review",
            "expected_action": None,
            "candidates": [],
        }
    ]
    calls: list[tuple[object, bool]] = []

    def fetch(config_arg, *, dry_run):
        calls.append((config_arg, dry_run))
        return diagnoses

    monkeypatch.setattr(seerr_reconciler, "fetch_and_diagnose_requests", fetch)

    assert seerr_reconciler.run_once(config, dry_run=True) == 0
    assert calls == [(config, True)]
    assert json.loads(state_path.read_text()) == {"requests": {"77:series:1": diagnoses[0]}}
    status = json.loads(status_path.read_text())
    assert status["diagnosed"] == 1
    assert status["blocked"] == 1
    assert status["mutated"] == 0


def test_run_once_dry_run_preserves_fallback_state_when_diagnosis_updates(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    status_path = tmp_path / "status.json"
    state_path.write_text(
        json.dumps(
            {
                "requests": {
                    "77:series:1": {
                        "state_key": "77:series:1",
                        "failed_guids": ["guid-1"],
                        "failed_hashes": ["hash-1"],
                        "hash": "active-hash",
                        "state": "fallback_grabbed",
                    }
                }
            }
        )
    )
    config = seerr_reconciler.ReconcilerConfig(
        enabled=False,
        fallback_enabled=False,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(state_path),
        status_path=str(status_path),
        status_max_age_seconds=900,
    )

    monkeypatch.setattr(
        seerr_reconciler,
        "fetch_and_diagnose_requests",
        lambda *_args, **_kwargs: [
            {
                "state_key": "77:series:1",
                "request_id": 77,
                "state": "blocked_no_candidate",
                "reason": "no candidate",
            }
        ],
    )

    assert seerr_reconciler.run_once(config, dry_run=True) == 0

    row = json.loads(state_path.read_text())["requests"]["77:series:1"]
    assert row["failed_guids"] == ["guid-1"]
    assert row["failed_hashes"] == ["hash-1"]
    assert row["hash"] == "active-hash"
    assert row["state"] == "blocked_no_candidate"


def test_run_once_disabled_without_dry_run_does_not_fetch(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    status_path = tmp_path / "status.json"
    config = seerr_reconciler.ReconcilerConfig(
        enabled=False,
        fallback_enabled=False,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(state_path),
        status_path=str(status_path),
        status_max_age_seconds=900,
    )

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("disabled reconciler must not fetch requests")

    monkeypatch.setattr(seerr_reconciler, "fetch_and_diagnose_requests", fail_fetch)

    assert seerr_reconciler.run_once(config, dry_run=False) == 0
    assert json.loads(state_path.read_text()) == {"requests": {}}
    status = json.loads(status_path.read_text())
    assert status["enabled"] is False
    assert status["diagnosed"] == 0
    assert status["blocked"] == 0
    assert status["mutated"] == 0


def test_run_once_enabled_applies_fallback_grab(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    status_path = tmp_path / "status.json"
    config = seerr_reconciler.ReconcilerConfig(
        enabled=True,
        fallback_enabled=True,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(state_path),
        status_path=str(status_path),
        status_max_age_seconds=900,
    )
    row = {
        "state_key": "77:series:1",
        "request_id": 77,
        "state": "fallback_candidate_found",
        "reason": "soft override allowed",
        "recommended_action": "grab_fallback",
        "expected_action": "grab:prowlarr:7:prow-guid",
        "candidates": [
            {"guid": "prow-guid", "indexer_id": 7, "title": "Stag S01", "size_bytes": 123}
        ],
    }

    class FakeProwlarr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(seerr_reconciler, "fetch_and_diagnose_requests", lambda *_args, **_kwargs: [row])
    monkeypatch.setattr(seerr_reconciler, "ProwlarrClient", FakeProwlarr)
    monkeypatch.setattr(
        seerr_reconciler,
        "grab_prowlarr_release",
        lambda *_args, **_kwargs: {"ok": True, "source": "prowlarr"},
    )
    monkeypatch.setattr(seerr_reconciler.time, "time", lambda: 1786881600)

    assert seerr_reconciler.run_once(config, dry_run=False) == 0

    status = json.loads(status_path.read_text())
    saved = json.loads(state_path.read_text())["requests"]["77:series:1"]
    assert status["mutated"] == 1
    assert saved["state"] == "fallback_grabbed"
    assert saved["attempts"] == [{"guid": "prow-guid", "ok": True, "at": 1786881600}]


def test_fetch_and_diagnose_uses_normalized_stable_ids_for_prowlarr(monkeypatch, tmp_path):
    config = seerr_reconciler.ReconcilerConfig(
        enabled=True,
        fallback_enabled=False,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(tmp_path / "state.json"),
        status_path=str(tmp_path / "status.json"),
        status_max_age_seconds=900,
    )
    calls: dict[str, object] = {}

    class FakeSeerr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def requests(self, **_kwargs):
            return [
                {
                    "id": 77,
                    "status": "approved",
                    "createdAt": "2026-08-16T11:59:59Z",
                    "media": {
                        "mediaType": "tv",
                        "title": "Stag",
                        "firstAirDate": "2024-01-01",
                        "tmdbId": 100,
                        "tvdbId": 200,
                        "externalServiceId": 243,
                    },
                    "seasons": [{"seasonNumber": 1}],
                }
            ]

    class FakeArr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, path, query=None):
            assert query is None
            assert path == "/series/243"
            return {"id": 243, "title": "Stag", "monitored": True}

    class FakeProwlarr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def search(self, **kwargs):
            calls.update(kwargs)
            return []

    class FakeQbt:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def torrents_info_raw(self):
            return []

    monkeypatch.setattr(seerr_reconciler, "SeerrClient", FakeSeerr)
    monkeypatch.setattr(seerr_reconciler, "ArrClient", FakeArr)
    monkeypatch.setattr(seerr_reconciler, "ProwlarrClient", FakeProwlarr)
    monkeypatch.setattr(seerr_reconciler, "QBittorrentClient", FakeQbt)
    monkeypatch.setattr(seerr_reconciler, "_arr_releases_for_request", lambda *_args: [])
    monkeypatch.setattr(seerr_reconciler.time, "time", lambda: 1786881600)

    rows = seerr_reconciler.fetch_and_diagnose_requests(config, dry_run=True)

    assert calls["tvdb_id"] == 200
    assert calls["tmdb_id"] == 100
    assert calls["query"] == "Stag S01"
    assert rows[0]["arr_id"] == 243


def test_fetch_and_diagnose_passes_arr_release_evidence(monkeypatch, tmp_path):
    config = seerr_reconciler.ReconcilerConfig(
        enabled=True,
        fallback_enabled=False,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(tmp_path / "state.json"),
        status_path=str(tmp_path / "status.json"),
        status_max_age_seconds=900,
    )
    arr_release = {
        "title": "Stag S01 1080p",
        "guid": "arr-rejected",
        "download_allowed": False,
        "rejections": ["Release is over maximum size"],
    }
    seen: dict[str, object] = {}

    class FakeSeerr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def requests(self, **_kwargs):
            return [
                {
                    "id": 77,
                    "status": "approved",
                    "createdAt": "2026-08-16T11:59:59Z",
                    "media": {
                        "mediaType": "tv",
                        "title": "Stag",
                        "firstAirDate": "2024-01-01",
                        "tmdbId": 100,
                        "tvdbId": 200,
                        "externalServiceId": 243,
                    },
                    "seasons": [{"seasonNumber": 1}],
                }
            ]

    class FakeArr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return {"id": 243, "title": "Stag", "monitored": True}

    class FakeProwlarr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def search(self, **_kwargs):
            return []

    class FakeQbt:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def torrents_info_raw(self):
            return []

    def fake_diagnose(request, **kwargs):
        seen["arr_releases"] = kwargs["arr_releases"]
        return type(
            "Result",
            (),
            {
                "request": request,
                "state": "blocked_no_candidate",
                "reason": "checked",
                "evidence": {},
                "recommended_action": "manual_review",
                "expected_action": None,
                "candidates": [],
            },
        )()

    monkeypatch.setattr(seerr_reconciler, "SeerrClient", FakeSeerr)
    monkeypatch.setattr(seerr_reconciler, "ArrClient", FakeArr)
    monkeypatch.setattr(seerr_reconciler, "ProwlarrClient", FakeProwlarr)
    monkeypatch.setattr(seerr_reconciler, "QBittorrentClient", FakeQbt)
    monkeypatch.setattr(seerr_reconciler, "_arr_releases_for_request", lambda *_args: [arr_release])
    monkeypatch.setattr(seerr_reconciler, "diagnose_request", fake_diagnose)
    monkeypatch.setattr(seerr_reconciler.time, "time", lambda: 1786881600)

    seerr_reconciler.fetch_and_diagnose_requests(config, dry_run=False)

    assert seen["arr_releases"] == [arr_release]


def test_fetch_and_diagnose_dry_run_does_not_issue_arr_search(monkeypatch, tmp_path):
    config = seerr_reconciler.ReconcilerConfig(
        enabled=True,
        fallback_enabled=False,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(tmp_path / "state.json"),
        status_path=str(tmp_path / "status.json"),
        status_max_age_seconds=900,
    )

    class FakeSeerr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def requests(self, **_kwargs):
            return [
                {
                    "id": 77,
                    "status": "approved",
                    "createdAt": "2026-08-16T11:59:59Z",
                    "media": {
                        "mediaType": "tv",
                        "title": "Stag",
                        "tmdbId": 100,
                        "tvdbId": 200,
                        "externalServiceId": 243,
                    },
                    "seasons": [{"seasonNumber": 1}],
                }
            ]

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return {"id": 243, "title": "Stag", "monitored": True}

        def search(self, **_kwargs):
            return []

        def torrents_info_raw(self):
            return []

    monkeypatch.setattr(seerr_reconciler, "SeerrClient", FakeSeerr)
    monkeypatch.setattr(seerr_reconciler, "ArrClient", FakeClient)
    monkeypatch.setattr(seerr_reconciler, "ProwlarrClient", FakeClient)
    monkeypatch.setattr(seerr_reconciler, "QBittorrentClient", FakeClient)
    monkeypatch.setattr(
        seerr_reconciler,
        "_arr_releases_for_request",
        lambda *_args: (_ for _ in ()).throw(AssertionError("dry-run searched Arr")),
    )
    monkeypatch.setattr(seerr_reconciler.time, "time", lambda: 1786881600)

    rows = seerr_reconciler.fetch_and_diagnose_requests(config, dry_run=True)

    assert rows[0]["state"] == "blocked_no_candidate"


def test_fetch_and_diagnose_respects_arr_search_cooldown(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "requests": {
                    "77:series:1": {
                        "last_arr_search_at": 1786881540,
                        "evidence": {
                            "arr_rejections": [
                                {
                                    "title": "Stag S01 cached",
                                    "guid": "cached",
                                    "rejections": ["Release is over maximum size"],
                                }
                            ]
                        },
                    }
                }
            }
        )
    )
    config = seerr_reconciler.ReconcilerConfig(
        enabled=True,
        fallback_enabled=False,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(state_path),
        status_path=str(tmp_path / "status.json"),
        status_max_age_seconds=900,
    )
    seen: dict[str, object] = {}

    class FakeSeerr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def requests(self, **_kwargs):
            return [
                {
                    "id": 77,
                    "status": "approved",
                    "createdAt": "2026-08-16T11:59:59Z",
                    "media": {
                        "mediaType": "tv",
                        "title": "Stag",
                        "tmdbId": 100,
                        "tvdbId": 200,
                        "externalServiceId": 243,
                    },
                    "seasons": [{"seasonNumber": 1}],
                }
            ]

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return {"id": 243, "title": "Stag", "monitored": True}

        def search(self, **_kwargs):
            return []

        def torrents_info_raw(self):
            return []

    def fake_diagnose(_request, **kwargs):
        seen["arr_releases"] = kwargs["arr_releases"]
        return type(
            "Result",
            (),
            {
                "request": _request,
                "state": "blocked_no_candidate",
                "reason": "checked cached",
                "evidence": {},
                "recommended_action": "manual_review",
                "expected_action": None,
                "candidates": [],
            },
        )()

    monkeypatch.setattr(seerr_reconciler, "SeerrClient", FakeSeerr)
    monkeypatch.setattr(seerr_reconciler, "ArrClient", FakeClient)
    monkeypatch.setattr(seerr_reconciler, "ProwlarrClient", FakeClient)
    monkeypatch.setattr(seerr_reconciler, "QBittorrentClient", FakeClient)
    monkeypatch.setattr(
        seerr_reconciler,
        "_arr_releases_for_request",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cooldown searched Arr")),
    )
    monkeypatch.setattr(seerr_reconciler, "diagnose_request", fake_diagnose)
    monkeypatch.setattr(seerr_reconciler.time, "time", lambda: 1786881600)

    seerr_reconciler.fetch_and_diagnose_requests(config, dry_run=False)

    assert seen["arr_releases"] == [
        {
            "title": "Stag S01 cached",
            "guid": "cached",
            "rejections": ["Release is over maximum size"],
        }
    ]


def test_fetch_and_diagnose_skips_requests_older_than_lookback(monkeypatch, tmp_path):
    config = seerr_reconciler.ReconcilerConfig(
        enabled=True,
        fallback_enabled=False,
        lookback_hours=24,
        interval_seconds=300,
        grace_seconds=900,
        search_cooldown_seconds=3600,
        state_path=str(tmp_path / "state.json"),
        status_path=str(tmp_path / "status.json"),
        status_max_age_seconds=900,
    )
    diagnosed_request_ids: list[int] = []

    class FakeSeerr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def requests(self, **_kwargs):
            return [
                {
                    "id": 1,
                    "status": "approved",
                    "createdAt": "2026-08-14T11:59:59Z",
                    "media": {"mediaType": "movie", "title": "Old", "tmdbId": 1},
                },
                {
                    "id": 2,
                    "status": "approved",
                    "createdAt": "2026-08-15T12:00:01Z",
                    "media": {"mediaType": "movie", "title": "Recent", "tmdbId": 2},
                },
            ]

    class FakeProwlarr:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def search(self, **_kwargs):
            return []

    class FakeQbt:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def torrents_info_raw(self):
            return []

    class FakeArr:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(seerr_reconciler, "SeerrClient", FakeSeerr)
    monkeypatch.setattr(seerr_reconciler, "ProwlarrClient", FakeProwlarr)
    monkeypatch.setattr(seerr_reconciler, "QBittorrentClient", FakeQbt)
    monkeypatch.setattr(seerr_reconciler, "_arr_client_for", lambda *_args: FakeArr())
    monkeypatch.setattr(seerr_reconciler, "_arr_library_status", lambda *_args, **_kwargs: {})

    def diagnose(request, **_kwargs):
        diagnosed_request_ids.append(request.request_id)
        return type(
            "Result",
            (),
            {
                "request": request,
                "state": "diagnosed",
                "reason": "recent",
                "evidence": {},
                "recommended_action": None,
                "expected_action": None,
                "candidates": [],
            },
        )()

    monkeypatch.setattr(seerr_reconciler, "diagnose_request", diagnose)
    monkeypatch.setattr(seerr_reconciler.time, "time", lambda: 1786881600)

    rows = seerr_reconciler.fetch_and_diagnose_requests(config, dry_run=True)

    assert diagnosed_request_ids == [2]
    assert [row["request_id"] for row in rows] == [2]
