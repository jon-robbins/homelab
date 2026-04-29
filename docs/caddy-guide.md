# Caddy Routing Guide

## Overview

- All routing defined in a single file: `caddy/Caddyfile`
- Plain Caddy (not caddy-docker-proxy) — no Docker labels, no auto-discovery
- Custom image (`local/caddy-cf:latest`) with Cloudflare DNS plugin for TLS
- Mounted read-only into the Caddy container at `/etc/caddy/Caddyfile`

## Architecture

Three top-level blocks in the Caddyfile:

1. **HTTPS primary domain** (`{$BASE_DOMAIN}`) — subpath routes for the Arr stack and utilities
2. **HTTPS subdomains** — dedicated subdomains for Plex, Jellyfin, Pi-hole, OpenClaw
3. **HTTP `:80` LAN** — plain HTTP for LAN IP, Tailscale, and hostname access (no TLS)

A global options block enables the admin API on `:2019`.

## Routing Rules

### `handle` vs `handle_path`

- `handle /path*` — preserves the path prefix; use for services with `UrlBase` set (Sonarr, Radarr, Prowlarr, Readarr)
- `handle_path /path*` — strips the path prefix; use for services without `UrlBase` (Overseerr, qBittorrent, Dashboard)
- **CRITICAL:** Arr services MUST use `handle` with a matching `UrlBase` in their `config.xml`, otherwise the SPA will request root-relative assets that Caddy can't route correctly.

### Current Routes

**Subpath routes (HTTPS `{$BASE_DOMAIN}`):**

| Service | Directive | Backend | UrlBase |
|---------|-----------|---------|---------|
| Sonarr | `handle /sonarr*` | `sonarr:8989` | `/sonarr` |
| Radarr | `handle /radarr*` | `radarr:7878` | `/radarr` |
| Prowlarr | `handle /prowlarr*` | `prowlarr:9696` | `/prowlarr` |
| Readarr | `handle /readarr*` | `readarr:8787` | `/readarr` |
| Overseerr | `handle_path /overseerr*` | `overseerr:5055` | none |
| qBittorrent | `handle_path /qbittorrent/*` | `qbittorrent:8080` | none |
| Dashboard | `handle_path /dashboard*` | `internal-dashboard:8080` | none |
| Dashy | `handle` (catch-all) | `dashy:8082` | none |

> qBittorrent also has a `redir /qbittorrent /qbittorrent/ 301` to normalize the trailing slash.

**Subdomain routes (HTTPS):**

| Subdomain | Backend | Notes |
|-----------|---------|-------|
| `plex.{$BASE_DOMAIN}` | `host.docker.internal:32400` | host-network |
| `jellyfin.{$BASE_DOMAIN}` | `host.docker.internal:8096` | host-network |
| `pihole.{$BASE_DOMAIN}` | `host.docker.internal:8083` | host-network |
| `openclaw.{$BASE_DOMAIN}` | `openclaw-gateway:18789` | bridge-network |

**HTTP `:80` LAN routes:**

The `:80` block mirrors the HTTPS subpath routes and adds a few LAN-only extras:

| Service | Directive | Backend |
|---------|-----------|---------|
| Sonarr | `handle /sonarr*` | `sonarr:8989` |
| Radarr | `handle /radarr*` | `radarr:7878` |
| Prowlarr | `handle /prowlarr*` | `prowlarr:9696` |
| Readarr | `handle /readarr*` | `readarr:8787` |
| Overseerr | `handle_path /overseerr*` | `overseerr:5055` |
| Jellyfin | `handle /jellyfin*` | `host.docker.internal:8096` |
| qBittorrent | `handle_path /qbittorrent*` | `qbittorrent:8080` |
| Dashboard | `handle_path /dashboard*` | `internal-dashboard:8080` |
| FlareSolverr | `handle /flaresolverr*` | `flaresolverr:8191` |
| Dashy | `handle` (catch-all) | `dashy:8082` |

All `:80` proxies forward `Host` and `X-Real-IP` headers upstream.

### Host-Network Services

Services with `network_mode: host` (Plex, Jellyfin, Pi-hole) are proxied via `host.docker.internal:<port>`. Caddy's compose entry has `extra_hosts: ["host.docker.internal:host-gateway"]` to resolve this. UFW must allow Docker bridge subnet (`172.16.0.0/12`) to reach these ports.

## TLS Configuration

- All HTTPS blocks use the DNS-01 challenge: `dns cloudflare {env.CLOUDFLARE_TOKEN}`
- HSTS enabled on every HTTPS block: `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- Certificates auto-managed by Caddy, stored in `/data`
- Compression (`gzip zstd`) enabled on the primary domain and `:80` blocks

## Adding a New Service

1. Add the service to the appropriate compose file under `compose/`.
2. Edit `caddy/Caddyfile`:
   - **Subpath:** add a `handle` or `handle_path` block under `{$BASE_DOMAIN}`.
   - **Subdomain:** add a new site block (e.g., `newservice.{$BASE_DOMAIN} { ... }`).
3. If using `handle` (path-preserving), set the service's `UrlBase` to match the path.
4. Add a matching entry in the `:80` block for LAN access.
5. Restart Caddy: `docker compose restart caddy`.

## Troubleshooting

### Validate Caddyfile syntax

```bash
docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile
```

### Check live config via admin API

```bash
curl -s http://localhost:2019/config/ | jq .
```

### Common Issues

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| **404 on subpath** | `handle` vs `handle_path` mismatch with service `UrlBase` | Align the directive with the service's UrlBase setting |
| **SPA assets broken** | Service uses root-relative paths but Caddy stripped the prefix | Switch from `handle_path` to `handle` and set `UrlBase` |
| **Service unreachable** | Container name doesn't match Caddyfile backend | Verify Docker DNS name (container/service name) |
| **Host-network 502** | UFW blocking Docker bridge → host traffic | Allow `172.16.0.0/12` to the service port |
| **TLS errors** | Missing or invalid Cloudflare token | Verify `CLOUDFLARE_TOKEN` is set in the Caddy container env |

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `BASE_DOMAIN` | Site address used in the Caddyfile (`{$BASE_DOMAIN}`) |
| `CLOUDFLARE_TOKEN` | DNS-01 ACME challenge credential for TLS |
| `CADDY_IMAGE` | Docker image tag (`local/caddy-cf:latest`) |

## Caddy Container Configuration

- **Image:** custom build from `caddy/Dockerfile` (`caddy:latest` + Cloudflare DNS plugin)
- **Mounts:** `caddy/Caddyfile:/etc/caddy/Caddyfile:ro`, `data/caddy/data:/data`, `data/caddy/config:/config`
- **Security:** `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`
- **Healthcheck:** `wget -q --spider http://localhost:2019/config/`
- **Admin API:** `:2019` (container-internal only)
