# Scripts layout

Host-side helpers. Long-running services live in `compose/docker-compose.*.yml` (included by the root `docker-compose.yml`); the canonical worker package lives at [`src/homelab_workers/`](../src/homelab_workers/).

| Path | Purpose |
|------|---------|
| [setup.sh](setup.sh) | First-run bootstrap: `.env` creation/update prompts, config templates, GPU detection note, compose validation |
| [fix-media-permissions.sh](fix-media-permissions.sh) | One-off host helper for media directory ownership/modes |
| [gh-actions-local.sh](gh-actions-local.sh) | Run the GitHub Actions workflow locally via [`act`](https://nektosact.com/) |

Tests live under [`tests/`](../tests/) at the repo root: `compose/`, `runtime/`, `integration/`, `workers/`.

### Worker runtime

Production retries use Sonarr/Radarr **Failed Download Handling** (no dedicated worker container).

```bash
