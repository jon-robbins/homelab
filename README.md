# Homelab (Docker Compose)

Three-stack homelab with a **proxy-first bridge model**:

- `docker-compose.network.yml`: edge and control-plane services
- `docker-compose.media.yml`: media + *arr + worker services
- `docker-compose.llm.yml`: local AI services

State lives in `./data/` (gitignored except selected templates in [.gitignore](.gitignore)).

| Compose file | What runs |
|--------------|-----------|
| [docker-compose.network.yml](docker-compose.network.yml) | Caddy, Pi-hole, Dashy, `cloudflared`, Tailscale |
| [docker-compose.media.yml](docker-compose.media.yml) | FlareSolverr, Prowlarr, Jackett, Sonarr, Radarr, Readarr, Overseerr, Plex, Jellyfin, qBittorrent, `arr-retry-worker`, `torrent-health-ui` |
| [docker-compose.llm.yml](docker-compose.llm.yml) | Ollama (GPU), static internal dashboard (nginx) |

Tracked templates you copy or edit locally: [data/cloudflared/config.yml.example](data/cloudflared/config.yml.example), [data/dashy/conf.yml.example](data/dashy/conf.yml.example). Host-only and worker code: [scripts/README.md](scripts/README.md).

## Prerequisites

- Linux, **Docker Engine** + **Compose v2** (`docker compose`)
- **`python3`** — local runs of worker CLIs and [scripts/tests/](scripts/tests/) (pytest optional; see below)
- **NVIDIA Container Toolkit** if you keep GPU lines in media (Plex/Jellyfin) and LLM compose
- Outbound HTTPS (bootstrap may download `gum` / `cloudflared` into `./bin/`)

## Checkout path

The directory name is not stored in Git. This README assumes you clone or move the repo to something like **`~/homelab`** and run all commands from that directory:

```bash
git clone <your-repo-url> homelab
cd homelab
```

Update IDE workspaces, systemd units, or docs that still point at an old path (e.g. `~/docker`). External tooling (e.g. Cursor skills) that hardcode a path should be updated the same way.

## First boot (`bootstrap.sh`)

```bash
./scripts/bootstrap.sh
```

Interactive wizard ([`gum`](https://github.com/charmbracelet/gum)) that typically:

1. Writes or merges **`.env`** (`PUID`, `PGID`, `TZ`; keeps existing keys such as API keys).
2. Runs **[`scripts/rewrite_compose.py`](scripts/rewrite_compose.py)** to patch **media** and **LLM** compose for **your** paths: e.g. `/mnt/media-hdd`, `/mnt/media-nvme`, Plex host path, `JELLYFIN_PublishedServerUrl`, and GPU on/off.
3. Prepares **`./data/cloudflared/`** and walks Cloudflare Tunnel CLI setup; you end up with local **`data/cloudflared/config.yml`** (from the example — never commit secrets).
4. Optionally creates Docker network **`homelab_net`** (required by the LLM compose file; referenced elsewhere as `external: true`).
5. Runs **`docker compose … config`** on all three files to validate YAML.

Skip the wizard only if you are comfortable copying [.env.example](.env.example) to `.env`, editing compose by hand, and following [data/cloudflared/README.md](data/cloudflared/README.md).

## Start services

From the repo root:

```bash
docker compose -f docker-compose.network.yml up -d
docker compose -f docker-compose.media.yml up -d
docker compose -f docker-compose.llm.yml up -d
```

Update images as needed with `--pull always` or targeted recreates.

---

## Network stack (`docker-compose.network.yml`)

`homelab_net` is the shared external bridge network. Caddy and Dashy run on this bridge. Pi-hole, Tailscale, and cloudflared remain host-networked as intentional exceptions.

| Service | Purpose |
|---------|---------|
| **caddy** | Reverse proxy and primary ingress. Published ports: **80/443**. Upstreams are Docker service names on `homelab_net` (or `host.docker.internal` for host-mode exceptions). |
| **pihole** | LAN DNS sinkhole; host mode for **TCP/UDP 53**. Admin UI defaults to **8083**. |
| **dashy** | Internal dashboard behind Caddy root route (`/`). Runs on bridge as `dashy:8082`. |
| **cloudflared** | Cloudflare Tunnel; config `data/cloudflared/config.yml`, credentials under `data/cloudflared/credentials/`. |
| **tailscale** | `tailscaled` in Docker host mode (`/dev/net/tun`, NET_ADMIN/NET_RAW). |

Create `homelab_net` once (bootstrap can do it): `docker network create homelab_net`.

---

## Media stack (`docker-compose.media.yml`)

Most services are now on `homelab_net` bridge and are intended to be reached via Caddy path routes, not direct host ports.

| Service | Typical port / path (defaults) |
|---------|--------------------------------|
| FlareSolverr / Prowlarr / Jackett / Sonarr / Radarr / Readarr / qBittorrent | Bridge-only behind Caddy (`/flaresolverr`, `/prowlarr`, `/jackett`, `/sonarr`, `/radarr`, `/readarr`, `/qbittorrent`). |
| Overseerr | Bridge service, plus loopback publish `127.0.0.1:5055` for Cloudflare Tunnel compatibility. |
| Plex | Host mode exception (discovery/streaming compatibility), direct port `32400` as needed. |
| Jellyfin | Host mode exception, direct port `8096` as needed. |
| `arr-retry-worker` | Bridge worker; defaults now use service URLs (`http://sonarr:8989/sonarr`, etc.). |
| `torrent-health-ui` | Bridge service on `torrent-health-ui:8091`, routed by Caddy at `/torrent-health`. |

Important after migration: app-to-app URLs that previously used `127.0.0.1` should now use service names on `homelab_net` (for example `qbittorrent:8080`, `flaresolverr:8191`, `sonarr:8989`).

---

## LLM stack (`docker-compose.llm.yml`)

Uses bridge network **`homelab_net`** (must exist).

| Service | Bind | Notes |
|---------|------|--------|
| **ollama** | `127.0.0.1:11434` | Model storage `./data/llm/ollama`; GPU enabled in default compose — disable in file or via bootstrap if no GPU. |
| **internal-dashboard** | `127.0.0.1:8088` | Static HTML from `./data/internal-dashboard/`. |

---

## Pi-hole (DNS)

- **Compose:** service **`pihole`** in [docker-compose.network.yml](docker-compose.network.yml); `network_mode: host`.
- **DNS:** host must allow Pi-hole to bind **`:53` TCP and UDP** (common fix: disable **`systemd-resolved`** stub listener `DNSStubListener=no`, restart it, ensure `/etc/resolv.conf` is not stuck on `127.0.0.53` only).
- **Admin UI:** **`http://<LAN-IP>:<PIHOLE_WEB_PORT>/admin/`** — default port **8083** so it does not fight Caddy on **80**. Env: **`PIHOLE_WEBPASSWORD`**, optional **`PIHOLE_DNS_`**, **`PIHOLE_WEB_PORT`** (wired to Pi-hole v6 **`FTLCONF_*`** in compose).
- **Clients:** set **DHCP DNS** on your router to this host’s LAN IP (not only “WAN DNS”); watch for IPv6/secondary DNS bypassing Pi-hole.

Do **not** put Pi-hole’s admin UI on a public tunnel hostname without extra auth and threat modeling.

---

## Secrets and Git

Never commit: **`.env`**, **`data/cloudflared/config.yml`**, **`data/cloudflared/credentials/*.json`**, certs/pem, or anything under **`./data/*`** that holds live app state. See [.gitignore](.gitignore) and [data/cloudflared/README.md](data/cloudflared/README.md) (permissions for UID `65532` / cloudflared).

---

## Security (operational)

- Proxy-first target is now in place: keep only justified host listeners (Pi-hole, Tailscale, media exceptions), and route management UIs through Caddy on the bridge.
- Prefer Cloudflare Tunnel only for hostnames you intentionally expose; keep qBittorrent, *arr, Pi-hole admin, FlareSolverr, etc. LAN/Tailscale only.
- After copying secrets or restoring backups: **`./hardening/secure-secret-file-permissions.sh`** from repo root.
- Optional host firewall: [hardening/nftables-arr-stack.nft](hardening/nftables-arr-stack.nft) — edit `local_v4` before `nft -f`.

## Exposure inventory (current model)

| Exposure | Needed for | Access intent |
|----------|------------|---------------|
| TCP 80/443 (Caddy) | Primary app ingress | LAN/Tailscale; optionally tunneled/public per policy |
| TCP+UDP 53 (Pi-hole) | DNS for LAN clients | LAN/Tailscale only |
| TCP 8083 (Pi-hole UI) | Pi-hole admin | LAN/Tailscale only |
| TCP 5055 (loopback bind) | cloudflared -> Overseerr | Host-local only |
| TCP 8096 (Jellyfin) | Media clients / tunnel target | LAN/public by explicit policy |
| TCP 32400 (Plex) | Plex clients / remote access | LAN/public by explicit policy |

---

## Tests

Install **pytest** (`pip install pytest` or distro package). [pytest.ini](pytest.ini) sets `pythonpath = scripts/workers` so tests can `import arr_retry`.

```bash
python3 -m pytest scripts/tests/
```
