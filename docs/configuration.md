# Configuration

[← Back to README](../README.md)

Environment contract, path layout, Caddy routing model, and adding new services.
For the live route table see [caddy-guide.md](caddy-guide.md).

## Configuration

Compose reads variables from `.env`. `scripts/ops/setup.sh` only updates `.env`; it does not rewrite compose files.

**Managed secrets** (API keys, tokens, qBittorrent credentials) are stored in Infisical; leave those keys blank in `.env`. The blank entries in `.env.example` are only a name contract for Compose, tests, and agents. The authoritative catalog is `scripts/lib/managed_secrets.py`; the runtime client is `scripts/lib/infisical_fetch.py`. `${HOME}/.config/homelab/infisical.env` stores only the Infisical bootstrap credential, not application tokens. See [secrets-management.md](secrets-management.md) for setup, path taxonomy, migration, and `./scripts/ops/audit-local-env-secrets.sh`.

### Core environment contract (`.env.example`)

| Variable | Required | Purpose | Default |
|---|---|---|---|
| `PUID` | Yes | UID used by LinuxServer containers | `1000` |
| `PGID` | Yes | GID used by LinuxServer containers | `1000` |
| `TZ` | Yes | Time zone for containers | `UTC` |
| `BASE_DOMAIN` | Yes | Domain root used by the Caddyfile for site addresses | `home.example.homelab.local` |
| `CADDY_IMAGE` | Yes | Caddy image tag to run | `local/caddy-cf:latest` |
| `CLOUDFLARED_CONFIG_PATH` | Yes | Runtime cloudflared config location | `./data/cloudflared/config.yml` |
| `CLOUDFLARED_CONFIG_TEMPLATE` | Yes | cloudflared template source path | `./config/cloudflared/config.yml.example` |
| `MEDIA_HDD_PATH` | Yes | Main media library mount | `/mnt/media-hdd` |
| `MEDIA_NVME_PATH` | Yes | Fast download/transcode mount | `/mnt/media-nvme` |
| `PLEX_DATA_PATH` | Yes | Plex metadata/config storage path | `/srv/plex` |
| `CLOUDFLARE_TOKEN` | No (recommended for public TLS + tunnel) | Cloudflare API token used by Caddy DNS challenge and tunnel auth | empty |

### Additional compose variables

Media, worker, and LLM services also read optional non-secret values. Managed secret names remain visible in `.env.example`, but their values must come from Infisical through the root Compose entrypoint `scripts/ops/homelab-compose.py` (or its thin `.sh` alias) or the host-scoped launcher `scripts/ops/run-with-secrets.sh`. Non-managed environment variables are unchanged and are never fetched merely because they exist in an Infisical folder.

### Path customization

Set `MEDIA_HDD_PATH`, `MEDIA_NVME_PATH`, and `PLEX_DATA_PATH` to real host mount points before first start. This is required for stable imports and consistent path mapping between Arr apps, download clients, and media servers.

### Caddy routing model

All routing is defined in `config/caddy/Caddyfile`. There are three top-level blocks:

1. **HTTPS primary domain** (`{$BASE_DOMAIN}`) — subpath routes for Arr services, Overseerr, qBittorrent, and the internal dashboard.
2. **HTTPS subdomains** (`plex.{$BASE_DOMAIN}`, `jellyfin.{$BASE_DOMAIN}`, etc.) — one block per service that needs its own hostname.
3. **HTTP `:80` LAN** — mirrors the subpath routes without TLS for direct LAN/IP/Tailscale access.

Route directive choice:

- **`handle /path*`** — keeps the path prefix intact. Use for services that have a `UrlBase` setting (Sonarr, Radarr, Prowlarr, Readarr).
- **`handle_path /path*`** — strips the prefix before proxying. Use for services without `UrlBase` (Overseerr, qBittorrent, dashboard).

## Adding New Services

Add the service to the right compose file, attach it to `homelab_net`, then add a route block to `config/caddy/Caddyfile`. Keep host port publishing off unless you have a protocol requirement.

To add a new service behind Caddy:

1. Add the service to the appropriate compose file.
2. Add a route block to `config/caddy/Caddyfile`:
   - Use `handle /path*` if the service has a UrlBase setting.
   - Use `handle_path /path*` if it doesn't.
3. Restart Caddy: `./scripts/ops/homelab-compose.py restart caddy`

Validate before starting:

```bash
./scripts/ops/homelab-compose.py config --quiet
./scripts/ops/homelab-compose.py up -d
```

`config --quiet` authenticates to Infisical, validates required keys, and statically renders Compose. It does not change runtime state.
