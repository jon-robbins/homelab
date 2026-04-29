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

| Service | Container Name | Internal Port | URL Base | Internal URL | Host URL (if exposed) |
|---------|---------------|---------------|----------|--------------|----------------------|
| Sonarr | `sonarr` | 8989 | `/sonarr` | `http://sonarr:8989/sonarr` | Not directly exposed |
| Radarr | `radarr` | 7878 | `/radarr` | `http://radarr:7878/radarr` | Not directly exposed |
| Prowlarr | `prowlarr` | 9696 | `/prowlarr` | `http://prowlarr:9696/prowlarr` | Not directly exposed |
| Readarr | `readarr` | 8787 | `/readarr` | `http://readarr:8787/readarr` | Not directly exposed |
| qBittorrent | `qbittorrent` | 8080 | — | `http://qbittorrent:8080` | Not directly exposed |
| Jellyfin | `jellyfin` | 8096 | — | `http://jellyfin:8096` | Not directly exposed |
| Caddy | `caddy` | 80/443 | — | `http://caddy:80` | Ports 80/443 exposed to host |

> **Caddy** acts as the ingress reverse proxy, routing external traffic to internal services. All other services are accessed through Caddy or directly via internal Docker DNS.

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

## 5. API Authentication

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

## 6. Common Pitfalls

- **`127.0.0.1` from the Docker host won't reach services** that don't publish ports — use container IPs or Docker DNS from within the network.
- **URL bases are mandatory in API paths.** `http://sonarr:8989/api/v3/` will 404; use `http://sonarr:8989/sonarr/api/v3/`.
- **qBittorrent returns HTTP 403** when credentials are missing or wrong in the Sonarr/Radarr download client config — these must be set in each app's UI, not just in `.env`.
- **Remote path mappings require the local path to exist** inside the container. Create the host directory first: `mkdir -p /mnt/media-nvme/Incoming/radarr`.
- **Container IPs are ephemeral** — they change on restart. Prefer DNS names when running within the Docker network.
- **All path strings in remote path mappings must end with a trailing slash** (e.g., `/downloads/radarr/`, not `/downloads/radarr`).

---

## 7. Volume Mount Reference

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

*Last updated: April 2026*
