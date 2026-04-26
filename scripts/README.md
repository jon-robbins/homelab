# Scripts layout

Host-side helpers. Long-running services live in `docker-compose.*.yml`; the canonical worker package lives at [`src/homelab_workers/`](../src/homelab_workers/).

| Path | Purpose |
|------|---------|
| [setup.sh](setup.sh) | First-run bootstrap: `.env` creation/update prompts, config templates, optional GPU overlay, compose validation |
| [fix-media-permissions.sh](fix-media-permissions.sh) | One-off host helper for media directory ownership/modes |
| [gh-actions-local.sh](gh-actions-local.sh) | Run the GitHub Actions workflow locally via [`act`](https://nektosact.com/) |

Tests live under [`tests/`](../tests/) at the repo root: `compose/`, `runtime/`, `integration/`, `workers/`.

### Worker runtime

The `arr-retry-worker` and `torrent-health-ui` services in [docker-compose.media.yml](../docker-compose.media.yml) mount `./src/homelab_workers/src` and run the package directly:

```bash
PYTHONPATH=/workspace/src/homelab_workers/src python -m homelab_workers.arr_retry.main ...
```
