# Arr retry and torrent health

This package backs the thin CLI [retry-sonarr-stalled-downloads.py](../retry-sonarr-stalled-downloads.py) (used by the `arr-retry-worker` service in [docker-compose.media.yml](../../../docker-compose.media.yml)).

- **Retry flow**: stalled queue cleanup, missing monitored search, optional force-grab fallback.
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
| `qbittorrent.py` | qBittorrent Web API (read-only for health) |
| `logic.py` | Arr release/stall logic |
| `health.py` | Policy + state |
| `processors.py` | Orchestration |
| `main.py` | Entry flow and cross-app health budget |

Tests: [`scripts/tests/test_health_policy.py`](../../tests/test_health_policy.py) (repo root [pytest.ini](../../../pytest.ini) sets `pythonpath` for pytest).
