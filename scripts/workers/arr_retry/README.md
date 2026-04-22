# Arr retry and torrent health

This package backs the thin CLI [retry-sonarr-stalled-downloads.py](../retry-sonarr-stalled-downloads.py) (used by the `arr-retry-worker` service in [docker-compose.media.yml](../../../docker-compose.media.yml)).

- **Retry flow**: stalled queue cleanup, missing monitored search, optional force-grab fallback.
- **qBittorrent orphan stalls (Sonarr)**: torrents in the Sonarr qB category (default `tv-sonarr`) in `stalledDL` / `missingFiles`, with no matching Sonarr queue row, are parsed to a library episode; if still monitored and missing, the worker schedules **EpisodeSearch** and **removes** that torrent from qBittorrent so a new grab can proceed. Tunables: `ARR_RETRY_QBT_ORPHAN_STALLS`, `SONARR_QBT_CATEGORY`, `ARR_RETRY_MAX_QB_ORPHANS`, `ARR_RETRY_QBT_ORPHAN_MIN_AGE_SECONDS`.
- **Missing + empty `/release` cache**: monitored missing episodes/movies with no cached indexer rows still get **EpisodeSearch** / **MoviesSearch** when `ARR_RETRY_MISSING_EMPTY_RELEASE_SEARCH` is true (default).
- **Optional health policy** (qBittorrent-backed): classify slow/aging items as `watch`, `replace`, or `race`; requires qBittorrent credentials in `.env` when enabled.

Health tuning keys and defaults are documented in the repo root [`.env.example`](../../../.env.example) (`ARR_HEALTH_*`).

## Run locally (dry-run first)

From the **repo root**, put `scripts/workers` on `PYTHONPATH` so `import arr_retry` resolves (same as the containers):

```bash
PYTHONPATH=scripts/workers python3 scripts/workers/retry-sonarr-stalled-downloads.py --help
```

Dry-run with health logic (no writes):

```bash
PYTHONPATH=scripts/workers python3 scripts/workers/retry-sonarr-stalled-downloads.py \
  --enable-health-replacement \
  --enable-health-race
```

Apply (use only after reviewing dry-run output):

```bash
PYTHONPATH=scripts/workers python3 scripts/workers/retry-sonarr-stalled-downloads.py \
  --apply \
  --enable-health-replacement \
  --enable-health-race
```

Configuration order: CLI flags → environment variables (including repo `.env` when present) → Arr `config.xml` API key fallback.

## Module layout

| File | Role |
|------|------|
| `args.py` | CLI and env loading |
| `client.py` | Sonarr/Radarr API |
| `qbittorrent.py` | qBittorrent Web API (reads + orphan torrent delete) |
| `logic.py` | Arr release/stall logic |
| `health.py` | Policy + state |
| `processors.py` | Orchestration |
| `main.py` | Entry flow and cross-app health budget |

Tests: [`scripts/tests/test_health_policy.py`](../../tests/test_health_policy.py) (repo root [pytest.ini](../../../pytest.ini) sets `pythonpath` for pytest).
