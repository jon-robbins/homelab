# Homelab (Docker Compose)

Personal homelab stack: **edge + DNS + tunnel** on one compose file, **media and *arr** on another, **LLM** on a third. Almost everything uses **`network_mode: host`** on the media and network stacks, so services listen on the host’s normal ports (not published `ports:` mappings). State lives under **`./data/`** (gitignored except a few templates — see [.gitignore](.gitignore)).

| Compose file | What runs |
|--------------|-----------|
| [docker-compose.network.yml](docker-compose.network.yml) | Caddy, Pi-hole, Dashy, `cloudflared`, Tailscale (`tailscaled`) |
| [docker-compose.media.yml](docker-compose.media.yml) | FlareSolverr, Prowlarr, Jackett, Sonarr, Radarr, Readarr, Overseerr, Plex, Jellyfin, qBittorrent, `arr-retry-worker`, `torrent-health-ui` |
| [docker-compose.llm.yml](docker-compose.llm.yml) | Ollama (GPU), static internal dashboard (nginx) |

Tracked templates you copy or edit locally: [data/cloudflared/config.yml.example](data/cloudflared/config.yml.example), [data/dashy/conf.yml.example](data/dashy/conf.yml.example). Host-only and worker code: [scripts/README.md](scripts/README.md).

## Prerequisites

- Linux, **Docker Engine** + **Compose v2** (`docker compose`)
- **`python3`** — local runs of worker CLIs and [scripts/tests/](scripts/tests/) (pytest optional; see below)
- **NVIDIA Container Toolkit** if you keep GPU lines in media (Plex/Jellyfin) and LLM compose
- Outbound HTTPS (bootstrap may download `gum` / `cloudflared` into `./bin/`)

## Repo checkout path

The directory name is not stored in Git. This README assumes you clone or move the repo to something like **`~/homelab`** and run all commands from that directory:

```bash
git clone <your-repo-url> homelab
cd homelab
```

Update IDE workspaces, systemd units, or docs that still point at an old path (e.g. `~/docker`). External tooling (e.g. Cursor skills) that hardcode a path should be updated the same way.

## First boot: `bootstrap.sh`

```bash
./scripts/bootstrap.sh
```

Interactive wizard ([`gum`](https://github.com/charmbracelet/gum)) that typically:

1. Writes or merges **`.env`** (`PUID`, `PGID`, `TZ`; keeps existing keys such as API keys).
2. Runs **[`scripts/rewrite_compose.py`](scripts/rewrite_compose.py)** to patch **media** and **LLM** compose for **your** paths: e.g. `/mnt/media-hdd`, `/mnt/media-nvme`, Plex host path, `JELLYFIN_PublishedServerUrl`, and GPU on/off.
3. Prepares **`./data/cloudflared/`** and walks **Cloudflare Tunnel** CLI steps; you end up with a local **`data/cloudflared/config.yml`** (from the example — never commit secrets).
4. Optionally creates Docker network **`homelab_net`** (required by the LLM compose file; referenced elsewhere as `external: true`).
5. Runs **`docker compose … config`** on all three files to validate YAML.

Skip the wizard only if you are comfortable copying [.env.example](.env.example) → `.env`, editing compose by hand, and following [data/cloudflared/README.md](data/cloudflared/README.md).

## Start and update services

From the repo root:

```bash
docker compose -f docker-compose.network.yml up -d
docker compose -f docker-compose.media.yml up -d
docker compose -f docker-compose.llm.yml up -d
```

Rebuild/pull after image changes: add `--pull always` or recreate specific services as needed.

---

## Network stack (`docker-compose.network.yml`)

All listed services use **`network_mode: host`** unless noted otherwise.

| Service | Purpose |
|---------|---------|
| **caddy** | Reverse proxy; reads [data/caddy/Caddyfile](data/caddy/Caddyfile) (not in git by default — create from your setup). Binds **`:80`** (and whatever you configure). |
| **pihole** | LAN DNS sinkhole; needs **TCP+UDP 53** on the host. Admin UI on **`PIHOLE_WEB_PORT`** (default **8083**) — see [Pi-hole](#pi-hole-dns) below. |
| **dashy** | Internal links dashboard; listens on **`127.0.0.1:8082`** only. Config: `data/dashy/conf.yml`. |
| **cloudflared** | Cloudflare Tunnel; config `data/cloudflared/config.yml`, credentials under `data/cloudflared/credentials/`. |
| **tailscale** | `tailscaled` in Docker; **edit `hostname:`** in the compose file to match your node name. |

`homelab_net` appears in this file as **external** so compose validates; creating the network is part of bootstrap or: `docker network create homelab_net`.

---

## Media stack (`docker-compose.media.yml`)

Every service here uses **`network_mode: host`**. Access URLs are therefore **`http://127.0.0.1:<port>`** on the server, or **`http://<server-LAN-IP>:<port>`** from your LAN. *Arr apps use LinuxServer defaults unless you changed them in each app’s config.

| Service | Typical port / path (defaults) |
|---------|--------------------------------|
| FlareSolverr | `8191` |
| Prowlarr | `9696` |
| Jackett | `9117` |
| Sonarr | `8989` (URL base often `/sonarr` if configured) |
| Radarr | `7878` |
| Readarr | `8787` |
| Overseerr | `5055` |
| Plex | `32400` (web `32400/web` as usual) |
| Jellyfin | `8096` |
| qBittorrent | **`8080`** (`WEBUI_PORT` in compose) |
| **arr-retry-worker** | Python `python:3.12-alpine`; cron every 15m; see [scripts/workers/arr_retry/README.md](scripts/workers/arr_retry/README.md) |
| **torrent-health-ui** | **`127.0.0.1:8091`** — small operator UI only |

**Mounts:** compose expects host paths such as **`/mnt/media-hdd`**, **`/mnt/media-nvme`**, and optional **`/srv/plex`** — `bootstrap` / `rewrite_compose.py` rewrites these for your machine. Sonarr uses **`PGID=988`** in this repo (download group); adjust if your host differs.

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
- **Clients:** set **DHCP DNS** on your router to this host’s LAN IP (not only “WAN DNS”); watch for **IPv6 DNS** or **secondary DNS** bypassing Pi-hole.

Do **not** put Pi-hole’s admin UI on a public tunnel hostname without extra auth and threat modeling.

---

## Secrets and Git

Never commit: **`.env`**, **`data/cloudflared/config.yml`**, **`data/cloudflared/credentials/*.json`**, certs/pem, or anything under **`./data/*`** that holds live app state. See [.gitignore](.gitignore) and [data/cloudflared/README.md](data/cloudflared/README.md) (permissions for UID `65532` / cloudflared).

---

## Security (operational)

- Prefer **Cloudflare Tunnel** only for hostnames you intend to be public; keep **qBittorrent, *arr, Pi-hole admin, FlareSolverr,** etc. on **LAN / Tailscale** only.
- After copying secrets or restoring backups: **`./hardening/secure-secret-file-permissions.sh`** from repo root.
- Optional host firewall: [hardening/nftables-arr-stack.nft](hardening/nftables-arr-stack.nft) — edit `local_v4` before `nft -f`.

---

## Tests

Install **pytest** (`pip install pytest` or distro package). [pytest.ini](pytest.ini) sets `pythonpath = scripts/workers` so tests can `import arr_retry`.

```bash
python3 -m pytest scripts/tests/
```
