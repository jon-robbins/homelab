# Scripts layout

Everything here is **repo source**. Long-running services are still defined in **`docker-compose.*.yml`**; host-side scripts run setup/migrations/tests while worker runtime code now comes from `src/homelab_workers`.

| Path | Purpose |
|------|---------|
| [media_action_router.py](media_action_router.py) | Strict two-phase LLM router: JSON-only action parse via Ollama -> validated `media-agent /action` call -> deterministic user-safe response |
| [setup.sh](setup.sh) | Main host setup flow: `.env` creation/update prompts, config templates, optional GPU overlay copy, compose validation |
| [fix-media-permissions.sh](fix-media-permissions.sh) | One-off host helper for media directory ownership/modes |
| [workers/](workers/) | Backward-compatible wrappers only; canonical worker source is `src/homelab_workers/src/homelab_workers/` |
| [tests/](tests/) | `pytest` for wrapper compatibility + migration helpers — e.g. `pytest scripts/tests/` |
| [../archive/legacy/2026-04-24/](../archive/legacy/2026-04-24/) | Archived legacy assets (ground-truth dataset + PR1918 image builder) with manifest |

### Workers (`PYTHONPATH`)

Services `arr-retry-worker` and `torrent-health-ui` set **`PYTHONPATH=/workspace/src/homelab_workers/src`** so imports resolve from the canonical package.

### Seerr PR #1918 preview (`overseerr-pr1918`)

1. Use the archived helper if needed: `bash archive/legacy/2026-04-24/scripts/build-seerr-pr1918.sh` (optional `SEERR_PR1918_CLONE_DIR`, `SEERR_PR1918_IMAGE`).
2. Clone prod config once (stop `overseerr` briefly for a clean SQLite copy if you can):

   ```bash
   docker compose -f docker-compose.media.yml stop overseerr
   cp -a ./data/overseerr ./data/overseerr-pr1918
   docker compose -f docker-compose.media.yml start overseerr
   docker compose -f docker-compose.media.yml up -d overseerr-pr1918
   ```

3. Reach UI at `http://<LAN-or-Tailscale-host>/seerr-pr1918/` (same `BASE_DOMAIN` as other apps). Set Seerr **application URL** (and OAuth redirects) to that base. Do not add this service to Cloudflare Tunnel until you intend to expose it publicly.

4. Optional: `python3 scripts/migrate-seerr-arr-hosts.py --settings ./data/overseerr-pr1918/settings.json --dry-run` then re-run without `--dry-run` after adding Readarr in the PR UI.
