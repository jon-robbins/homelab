# Network Access & Port Mapping Guide

Quick-reference for reaching services in this homelab stack — from scripts, containers, or the host.

---

## 1. Docker Network Topology

- All services run on a shared Docker bridge network: **`homelab_net`**
  ```bash
  docker network create homelab_net
  ```
- Services are **not** exposed to the host via port bindings by default. They communicate internally using container DNS names on the bridge network.
- Bridge subnet is typically `172.20.0.0/16`. Verify with:
  ```bash
  docker network inspect homelab_net --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
  ```

---

## 2. Service Access Patterns

| Service | Container Name | Internal Port | URL Base | Internal URL | Host URL (if exposed) | External URL |
|---------|---------------|---------------|----------|--------------|----------------------|-------------|
| Sonarr | `sonarr` | 8989 | `/sonarr` | `http://sonarr:8989/sonarr` | Not directly exposed | `https://{BASE_DOMAIN}/sonarr` |
| Radarr | `radarr` | 7878 | `/radarr` | `http://radarr:7878/radarr` | Not directly exposed | `https://{BASE_DOMAIN}/radarr` |
| Prowlarr | `prowlarr` | 9696 | `/prowlarr` | `http://prowlarr:9696/prowlarr` | Not directly exposed | `https://{BASE_DOMAIN}/prowlarr` |
| Readarr | `readarr` | 8787 | `/readarr` | `http://readarr:8787/readarr` | Not directly exposed | `https://{BASE_DOMAIN}/readarr` |
| Overseerr | `overseerr` | 5055 | — | `http://overseerr:5055` | Not directly exposed | `https://{BASE_DOMAIN}/overseerr` |
| qBittorrent | `qbittorrent` | 8080 | — | `http://qbittorrent:8080` | Not directly exposed | `https://{BASE_DOMAIN}/qbittorrent/` |
| Jellyfin | `jellyfin` | 8096 | — | `http://jellyfin:8096` | Not directly exposed | `https://jellyfin.{BASE_DOMAIN}` |
| Plex | `plex` | 32400 | — | `http://host.docker.internal:32400` | Port 32400 on host | `https://plex.{BASE_DOMAIN}` |
| Pi-hole | `pihole` | 8083 | — | `http://host.docker.internal:8083` | Port 8083 on host | `https://pihole.{BASE_DOMAIN}` |
| OpenClaw | `openclaw-gateway` | 18789 | — | `http://openclaw-gateway:18789` | Not directly exposed | `https://openclaw.{BASE_DOMAIN}` |
| Caddy | `caddy` | 80/443 | — | `http://caddy:80` | Port 80 + 8443 (HTTPS) exposed to host; public 443 reserved for Xray | — (ingress itself) |
| 3x-UI (Xray) | `3x-ui` | 26435/2096/443 | `/vpn` | `http://127.0.0.1:26435/vpn` | Port 443 (Reality), 26435 (panel), 2096 (subs) via `network_mode: host` | `https://{BASE_DOMAIN}/vpn` |

> **Caddy** acts as the ingress reverse proxy, routing external traffic to internal services on port 80 (HTTP) and 8443 (HTTPS). **Xray Reality** owns port 443 on the host for VPN traffic — see `3x-ui-vpn-setup.md` for details. All other services are accessed through Caddy or directly via internal Docker DNS.

---

## 3. Accessing Services from Scripts on the Host

**Do NOT use `127.0.0.1` or `localhost`** to reach services from the Docker host unless the service explicitly publishes ports. Most services in this setup do not.

### Option A — Use container IPs directly

```bash
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' sonarr
```

These IPs can change on restart — use only for ad-hoc debugging.

### Option B — Run the script inside the Docker network

```bash
docker run --rm --network homelab_net python:3.12-slim \
  python -c "import urllib.request; print(urllib.request.urlopen('http://sonarr:8989/sonarr/api/v3/system/status').read())"
```

### Option C — Use environment variable overrides

The `configure_remote_path_mappings.py` script supports `SONARR_URL` and `RADARR_URL` env vars:

```bash
SONARR_URL=http://sonarr:8989/sonarr RADARR_URL=http://radarr:7878/radarr \
  docker run --rm --network homelab_net \
  -v $(pwd)/scripts:/scripts \
  -v $(pwd)/.env:/app/.env \
  python:3.12-slim \
  python /scripts/configure_remote_path_mappings.py
```

---

## 4. Accessing Host Services from Containers

When a container needs to reach a service running directly on the host (e.g., Plex bound to host network):

1. Use `host.docker.internal` instead of `127.0.0.1`.
2. Add to the container's compose definition:
   ```yaml
   extra_hosts:
     - "host.docker.internal:host-gateway"
   ```
3. Ensure the host firewall allows inbound traffic from the Docker bridge subnet:
   ```bash
   # UFW example
   ufw allow from 172.20.0.0/16 to any port 32400  # Plex
   ```

---

## 5. External Access via Caddy

All services are behind the **Caddy** reverse proxy, which handles TLS termination (via Cloudflare DNS-01 challenge) and routing. No service is directly exposed to the internet — Caddy is the single ingress point.

### Subpath services (single hostname)

Services with a `UrlBase` setting are routed under `https://{BASE_DOMAIN}/<path>`:

- `https://{BASE_DOMAIN}/sonarr`
- `https://{BASE_DOMAIN}/radarr`
- `https://{BASE_DOMAIN}/prowlarr`
- `https://{BASE_DOMAIN}/readarr`
- `https://{BASE_DOMAIN}/overseerr`
- `https://{BASE_DOMAIN}/qbittorrent/`

### Subdomain services

Services that need their own hostname use dedicated subdomains:

- `https://plex.{BASE_DOMAIN}`
- `https://jellyfin.{BASE_DOMAIN}`
- `https://pihole.{BASE_DOMAIN}`
- `https://openclaw.{BASE_DOMAIN}`

### HTTP LAN access (port 80)

The `:80` block mirrors the subpath routes without TLS for direct access via LAN IP, Tailscale hostname (`100.x.x.x`), or `*.ts.net`. This is useful for local clients that don't need HTTPS.

> **Reference:** All routing is defined in [`caddy/Caddyfile`](../caddy/Caddyfile).

---

## 6. API Authentication

| Service | Auth Method | Header/Mechanism |
|---------|------------|-----------------|
| Sonarr | API key | `X-Api-Key: <key>` |
| Radarr | API key | `X-Api-Key: <key>` |
| Prowlarr | API key | `X-Api-Key: <key>` |
| Readarr | API key | `X-Api-Key: <key>` |
| qBittorrent | Session cookie | POST `/api/v2/auth/login` → cookie |

- API keys live in `.env` (`SONARR_API_KEY`, `RADARR_API_KEY`, etc.) and in each service's `data/<service>/config.xml`.
- The API base path **includes** the URL base:
  ```
  http://sonarr:8989/sonarr/api/v3/system/status
  http://radarr:7878/radarr/api/v3/movie
  ```

---

## 7. Common Pitfalls

- **`127.0.0.1` from the Docker host won't reach services** that don't publish ports — use container IPs or Docker DNS from within the network.
- **URL bases are mandatory in API paths.** `http://sonarr:8989/api/v3/` will 404; use `http://sonarr:8989/sonarr/api/v3/`.
- **qBittorrent returns HTTP 403** when credentials are missing or wrong in the Sonarr/Radarr download client config — these must be set in each app's UI, not just in `.env`.
- **Remote path mappings require the local path to exist** inside the container. Create the host directory first: `mkdir -p /mnt/media-nvme/Incoming/radarr`.
- **Container IPs are ephemeral** — they change on restart. Prefer DNS names when running within the Docker network.
- **All path strings in remote path mappings must end with a trailing slash** (e.g., `/downloads/radarr/`, not `/downloads/radarr`).

---

## 8. Volume Mount Reference

| Service | Container Path | Host Path | Purpose |
|---------|---------------|-----------|---------|
| Sonarr | `/tv` | `/mnt/media-hdd/TV` | TV library |
| Sonarr | `/downloads` | `/mnt/media-nvme/Incoming` | Download staging |
| Radarr | `/movies` | `/mnt/media-hdd/Movies` | Movie library |
| Radarr | `/downloads` | `/mnt/media-nvme/Incoming` | Download staging |
| Readarr | `/books` | `/mnt/media-hdd/Books` | Book library |
| Readarr | `/downloads` | `/mnt/media-nvme/Incoming` | Download staging |
| qBittorrent | `/downloads` | `/mnt/media-nvme/Incoming` | Active downloads |
| qBittorrent | `/movies` | `/mnt/media-hdd/Movies` | Direct movie access |
| qBittorrent | `/tv` | `/mnt/media-hdd/TV` | Direct TV access |

---

*Last updated: May 2026*
