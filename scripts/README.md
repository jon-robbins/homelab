# Scripts layout

Everything here is **repo source**. Long-running services are still defined in **`docker-compose.*.yml`**; these files are what containers mount (e.g. `./scripts:/workspace/scripts:ro`) or what you run on the host during setup.

| Path | Purpose |
|------|---------|
| [bootstrap.sh](bootstrap.sh) | Host wizard: `.env`, compose rewrites, optional `homelab_net`, tunnel bootstrap |
| [rewrite_compose.py](rewrite_compose.py) | Called by `bootstrap.sh` — patches compose YAML for your paths and GPU |
| [fix-media-permissions.sh](fix-media-permissions.sh) | One-off host helper for media directory ownership/modes |
| [workers/](workers/) | **All** Compose worker code: thin CLIs, small UIs, and the [`arr_retry`](workers/arr_retry/) Python package (Sonarr/Radarr retry + optional qBittorrent health policy) |
| [tests/](tests/) | `pytest` for `arr_retry` — run from repo root: `python3 -m pytest scripts/tests/` ([pytest.ini](../pytest.ini) adds `scripts/workers` to `pythonpath`) |

### Workers (`PYTHONPATH`)

Services `arr-retry-worker` and `torrent-health-ui` set **`PYTHONPATH=/workspace/scripts/workers`** so `import arr_retry` resolves to `./workers/arr_retry/` inside the mount.

### Historical note

A **GCP tinyproxy + SSH tunnel** layout for Prowlarr egress used to live under `scripts/gcp-proxy/`; it was removed from the stack and the empty tree deleted. [.env.example](../.env.example) still mentions it only as deprecated context.
