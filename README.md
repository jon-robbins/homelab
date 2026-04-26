# Homelab Docker Template

Practical Docker Compose homelab with path-based ingress, media automation, and optional local LLM tooling.

**Checkout path:** Clone to any directory you like. Scripts use paths relative to the repository root (for example `~/homelab` instead of a folder named `docker`).

![License](https://img.shields.io/badge/license-MIT-green)
![Docker Compose](https://img.shields.io/badge/docker%20compose-v2+-blue)

## Architecture Diagram

![Homelab architecture](docs/images/readme-architecture.svg)

<details>
<summary>diagram source</summary>

```mermaid
flowchart TB
    user[User Devices]

    subgraph host["Host Network Services (intentional exceptions)"]
        pihole[Pi-hole\nDNS on :53]
        tailscale[Tailscale\nprivate remote access]
        cloudflared[cloudflared\nCloudflare Tunnel agent]
        plex[Plex\nLAN discovery + casting]
        jellyfin[Jellyfin\nLAN discovery + casting]
    end

    subgraph homelab["homelab_net bridge (shared service mesh)"]
        caddy[caddy-docker-proxy\ningress on :80/:443]
        dashy[Dashy]
        overseerr[Overseerr]
        mediaagent[media-agent]
        ollama[Ollama]
        openclaw[openclaw-gateway]
        dashboard[internal-dashboard]
    end

    subgraph media["media_internal group (logical; implemented on homelab_net)"]
        sonarr[Sonarr]
        radarr[Radarr]
        readarr[Readarr]
        prowlarr[Prowlarr]
        jackett[Jackett]
        qb[qBittorrent]
        flaresolverr[FlareSolverr]
        arrretry[arr-retry-worker]
        thui[torrent-health-ui]
    end

    subgraph storage["Host Storage Mounts"]
        data[(./data/* runtime state)]
        mediahdd[${MEDIA_HDD_PATH}]
        medianvme[${MEDIA_NVME_PATH}]
        plexdata[${PLEX_DATA_PATH}]
    end

    user -->|HTTPS| caddy
    user -->|DNS queries| pihole
    user -->|Private mesh access| tailscale
    cloudflared -->|Tunnel ingress| caddy
    caddy -->|Path routing labels| dashy
    caddy -->|/overseerr| overseerr
    caddy -->|/sonarr /radarr /readarr| sonarr
    caddy -->|/qbittorrent| qb
    caddy -->|openclaw.<domain>| openclaw
    caddy -->|/ollama| ollama
    arrretry -->|API retries| sonarr
    arrretry -->|API retries| radarr
    arrretry -->|Torrent health checks| qb
    sonarr -->|Indexer queries| prowlarr
    radarr -->|Indexer queries| prowlarr
    readarr -->|Indexer queries| prowlarr
    prowlarr -->|Bypass anti-bot flows| flaresolverr
    mediaagent -->|Reads Arr metadata| sonarr
    mediaagent -->|Reads Arr metadata| radarr
    openclaw -->|Tooling calls| mediaagent

    dashy --- data
    sonarr --- data
    radarr --- data
    readarr --- data
    prowlarr --- data
    qb --- data
    ollama --- data
    cloudflared --- data
    plex --- mediahdd
    plex --- medianvme
    plex --- plexdata
    jellyfin --- mediahdd
    jellyfin --- medianvme
```

</details>

## Features

- Split stack model: `docker-compose.network.yml`, `docker-compose.media.yml`, and `docker-compose.llm.yml`.
- First-run bootstrap with `scripts/setup.sh` for `.env`, templates, Docker network, and compose validation.
- Label-driven ingress with `lucaslorentz/caddy-docker-proxy` (routing stays next to each service).
- Intentional host networking only for DNS, tunnel/VPN, and media discovery workloads.
- Optional NVIDIA GPU overlay (`docker-compose.gpu.yml`) generated from `config/gpu/docker-compose.gpu.yml`.
- Python workers ship from the [`src/homelab_workers`](src/homelab_workers/) package, mounted directly into the worker containers.

## Quick Start

### Prerequisites

- Docker and Docker Compose v2+
- Linux host (Ubuntu 22.04+ recommended)
- Optional: NVIDIA GPU with NVIDIA drivers and `nvidia-smi`

### Setup

```bash
git clone <repo-url> ~/homelab
cd ~/homelab
./scripts/setup.sh
```

`scripts/setup.sh` creates `.env` from `.env.example` when missing, prompts for host path values, copies config templates, creates `homelab_net` if needed, validates compose files, and conditionally creates `docker-compose.gpu.yml`.

### Start Services

```bash
docker compose -f docker-compose.network.yml up -d
docker compose -f docker-compose.media.yml up -d
docker compose -f docker-compose.llm.yml up -d  # optional
```

If GPU overlay is enabled:

```bash
docker compose -f docker-compose.media.yml -f docker-compose.gpu.yml up -d
docker compose -f docker-compose.llm.yml -f docker-compose.gpu.yml up -d
```

## Repository Structure

```text
.
├── docker-compose.network.yml        # Edge services (Caddy, DNS, remote access)
├── docker-compose.media.yml          # Arr stack, Plex/Jellyfin, qBittorrent, workers, media-agent
├── docker-compose.llm.yml            # Ollama, internal dashboard, OpenClaw gateway
├── .env.example                      # Baseline environment contract
├── config/
│   ├── cloudflared/config.yml.example
│   ├── dashy/conf.yml.example
│   └── gpu/docker-compose.gpu.yml    # Source template for runtime GPU overlay
├── scripts/
│   ├── setup.sh
│   ├── README.md
│   ├── workers/
│   └── tests/
├── src/homelab_workers/
│   ├── pyproject.toml
│   └── src/homelab_workers/
├── hardening/
└── data/                             # Runtime state (gitignored)
```

## CI/CD

All development happens on the `dev` branch. Production runs from `main`.

![CI/CD flow](docs/images/readme-ci-cd.svg)

<details>
<summary>diagram source</summary>

```mermaid
flowchart LR
    push["push to dev"] --> validate["CI: validate"]
    cron["2 AM CEST nightly"] --> merge["merge dev → main"]
    merge --> deploy["deploy + E2E"]
    deploy -- pass --> done["production updated"]
    deploy -- fail --> rollback["revert + redeploy + issue"]
```

</details>

**Validate** (every push to `dev` and every PR):
- Compose config validation
- `docker compose build` for all local images
- pytest + ruff for workers and media-agent
- Bash syntax checks and README structure

**Nightly deploy** (2 AM CEST, or manual via workflow dispatch):
1. Skips if `dev` has no new commits or if the latest validate is failing.
2. Fast-forward merges `dev` into `main` and pushes.
3. Pulls images and runs `docker compose up -d --build` on the server.
4. Waits for all healthchecks (up to 5 minutes).
5. Runs E2E integration tests against the live stack.
6. On failure: reverts the merge, redeploys the previous version, and creates a GitHub issue.

To trigger a deploy without waiting for the nightly schedule:

```bash
gh workflow run "Nightly Deploy"
```

## Configuration

Compose reads variables from `.env`. `scripts/setup.sh` only updates `.env`; it does not rewrite compose files.

### Core environment contract (`.env.example`)

| Variable | Required | Purpose | Default |
|---|---|---|---|
| `PUID` | Yes | UID used by LinuxServer containers | `1000` |
| `PGID` | Yes | GID used by LinuxServer containers | `1000` |
| `TZ` | Yes | Time zone for containers | `UTC` |
| `BASE_DOMAIN` | Yes | Domain root used by Caddy labels | `home.ashorkqueen.xyz` |
| `CADDY_IMAGE` | Yes | Caddy image tag to run | `local/caddy-cf:latest` |
| `CADDY_INGRESS_NETWORKS` | Yes | Docker network(s) Caddy watches for labels | `homelab_net` |
| `PIHOLE_WEB_PORT` | Yes | Pi-hole web admin port on host | `8083` |
| `DASHY_CONFIG_PATH` | Yes | Runtime Dashy config location | `./data/dashy/conf.yml` |
| `DASHY_CONFIG_TEMPLATE` | Yes | Dashy template source path | `./config/dashy/conf.yml.example` |
| `CLOUDFLARED_CONFIG_PATH` | Yes | Runtime cloudflared config location | `./data/cloudflared/config.yml` |
| `CLOUDFLARED_CONFIG_TEMPLATE` | Yes | cloudflared template source path | `./config/cloudflared/config.yml.example` |
| `MEDIA_HDD_PATH` | Yes | Main media library mount | `/mnt/media-hdd` |
| `MEDIA_NVME_PATH` | Yes | Fast download/transcode mount | `/mnt/media-nvme` |
| `PLEX_DATA_PATH` | Yes | Plex metadata/config storage path | `/srv/plex` |
| `CLOUDFLARE_TOKEN` | No (recommended for public TLS + tunnel) | Cloudflare API token used by Caddy DNS challenge and tunnel auth | empty |

### Additional compose variables

Media, worker, and LLM services also read optional values (for example: Arr API keys, qBittorrent credentials, OpenClaw tokens, and media-agent token). Leave them blank in `.env` until you enable those features.

### Path customization

Set `MEDIA_HDD_PATH`, `MEDIA_NVME_PATH`, and `PLEX_DATA_PATH` to real host mount points before first start. This is required for stable imports and consistent path mapping between Arr apps, download clients, and media servers.

### Caddy label and path routing model

This stack uses `caddy-docker-proxy`: labels define the route contract, and Caddy regenerates config when containers change.

| Label | What it does | Example |
|---|---|---|
| `caddy` | Selects host/domain matcher | `caddy: "${BASE_DOMAIN}"` |
| `caddy.handle_path` | Matches and strips a path prefix | `caddy.handle_path: "/overseerr*"` |
| `caddy.handle_path.0_reverse_proxy` | Proxies stripped request to container port | `caddy.handle_path.0_reverse_proxy: "{{upstreams 5055}}"` |
| `caddy.reverse_proxy` | Direct host-level proxy (used for host-mode services) | `caddy.reverse_proxy: "host.docker.internal:32400"` |

Why this model: you keep ingress definitions next to each service, avoid static proxy drift, and make stack modules easier to reuse.

## Adding New Services

Add the service to the right compose file, attach it to `homelab_net`, then define Caddy labels. Keep host port publishing off unless you have a protocol requirement.

```yaml
services:
  bazarr:
    image: lscr.io/linuxserver/bazarr:latest
    container_name: bazarr
    networks:
      - homelab_net
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - TZ=${TZ}
    volumes:
      - ./data/bazarr:/config
      - ${MEDIA_HDD_PATH:-/mnt/media-hdd}:/media
    labels:
      caddy: "${BASE_DOMAIN}"
      caddy.handle_path: "/bazarr*"
      caddy.handle_path.0_reverse_proxy: "{{upstreams 6767}}"
    restart: unless-stopped
```

Validate before starting:

```bash
docker compose -f docker-compose.media.yml config --quiet
docker compose -f docker-compose.media.yml up -d
```

## GPU Acceleration

GPU support is overlay-based so CPU-only hosts can run the same base compose files. The source overlay lives at `config/gpu/docker-compose.gpu.yml`.

During setup, `scripts/setup.sh` checks `nvidia-smi`, asks for confirmation, and copies the overlay to `docker-compose.gpu.yml` only when you enable it. If detection fails or you decline, the runtime overlay file is removed to keep compose commands reproducible.

## Network Architecture

Bridge networking is the default because it limits exposure and keeps service-to-service DNS stable. Host mode is used only where protocol behavior requires it.

| Service | Network mode | Why this mode is used | Security trade-off |
|---|---|---|---|
| `pihole` | `host` | Needs direct DNS bind on `53/tcp` and `53/udp` | Broader host surface; harden host and admin auth |
| `tailscale` | `host` | Needs `/dev/net/tun` and low-level networking | Elevated capabilities (`NET_ADMIN`, `NET_RAW`) |
| `cloudflared` | `host` | Tunnel agent sits on host ingress/egress boundary | Treat as edge component; protect tokens |
| `plex` | `host` | Improves LAN discovery and client compatibility | Media service directly reachable on host |
| `jellyfin` | `host` | Improves LAN discovery and client compatibility | Media service directly reachable on host |
| Most others (`caddy`, Arr apps, workers, LLM services, media-agent) | `bridge` on `homelab_net` | Internal-only mesh with Caddy ingress | Smaller attack surface and centralized routing |

## Data Flow Diagram

![Media data flow](docs/images/readme-data-flow.svg)

<details>
<summary>diagram source</summary>

```mermaid
flowchart LR
    user[User]
    overseerr[Overseerr]
    sonarr[Sonarr]
    radarr[Radarr]
    prowlarr[Prowlarr]
    qbit[qBittorrent]
    plex[Plex]
    jellyfin[Jellyfin]
    arrretry[arr-retry-worker]

    user -->|Request movie/series| overseerr
    overseerr -->|Approved request| sonarr
    overseerr -->|Approved request| radarr
    sonarr -->|Searches indexers| prowlarr
    radarr -->|Searches indexers| prowlarr
    prowlarr -->|Sends release| qbit
    qbit -->|Completed download| sonarr
    qbit -->|Completed download| radarr
    sonarr -->|Library refresh| plex
    sonarr -->|Library refresh| jellyfin
    radarr -->|Library refresh| plex
    radarr -->|Library refresh| jellyfin
    arrretry -->|Retries stalled/missing items| sonarr
    arrretry -->|Retries stalled/missing items| radarr
    arrretry -->|Checks torrent state| qbit
```

</details>

## Python Workers

The source of truth for packaged workers is `src/homelab_workers` (`pyproject.toml`, package code, tests). It ships two CLI entry points: `arr-retry` and `torrent-health-ui`.

The `arr-retry-worker` and `torrent-health-ui` services mount `./src/homelab_workers/src` and run the package directly with `PYTHONPATH=/workspace/src/homelab_workers/src` (see [docker-compose.media.yml](docker-compose.media.yml)).

Install for local development:

```bash
cd src/homelab_workers
python3 -m pip install -e ".[dev]"
```

## Extend Media-Agent Capabilities

Each agent-callable capability is a small **action handler** under `media-agent/app/actions/`. The LLM parser (`app/router/parser.py`) and the strict `/action` payload union (`app/models/actions.py`) are driven from the same registry, so a new action is one coherent unit of work.

### Where to add things

- `media-agent/app/actions/<name>.py`
  - One module per action: Pydantic args model, `@register_action` handler with `run()` (and, if the router needs it, `run_for_router()`), `format_response`, and `selection_to_grab` for option lists.
- `media-agent/app/actions/__init__.py`
  - Import the new module so the registry is populated at startup.
- `media-agent/app/models/actions.py`
  - Add the action’s payload class and add it to the `ActionCall` union / discriminator.
- `media-agent/app/router/parser.py` / `app/router/intent.py`
  - Usually no edits: the router schema and allowed-field map are built from `registry.all_handlers()`. Touch these only for special parsing or intent rules.
- `media-agent/app/integrations/`
  - Low-level HTTP to Sonarr, Radarr, Prowlarr, qBittorrent, Ollama. Keep I/O here; business flow stays in `app/actions` or `app/services/`.
- `media-agent/app/services/`
  - Reusable non-route helpers (release ranking, torrent-name matching, qB file selection).

### Fast extension checklist

1. Add a Pydantic model in `app/models/actions.py` and include it in `ActionCall`.
2. Create `app/actions/<your_action>.py` with `register_action`, `run`, and any router overrides. Import it from `app/actions/__init__.py`.
3. Add tests under `media-agent/tests/api/` or `media-agent/tests/unit/`.
4. Run:

```bash
cd media-agent && python -m pytest -q
cd media-agent && ruff check app tests
```

## Security

Security relies on network segmentation, explicit ingress labels, and low-privilege container defaults. The stack applies `no-new-privileges` and `cap_drop: [ALL]` broadly, and services are exposed through Caddy/Tailscale/Cloudflare intentionally rather than through extra host ports.

### Hardening scripts

Run `scripts/setup.sh --harden` to apply file-permission lockdown and host firewall rules in one step. You can also run the scripts individually:

| Script | What it does |
|---|---|
| `hardening/secure-secret-file-permissions.sh` | `chmod 600` on `.env`, Arr config XMLs, qBittorrent config, and other secret-bearing files. Run after restoring configs or secrets. |
| `hardening/nftables-arr-stack.nft` | Host INPUT filter that restricts management ports (Caddy, DNS, media UIs) to RFC 1918 + Tailscale CGNAT ranges. Safe to reload (deduplicates automatically). |

```bash
# Apply both at once
./scripts/setup.sh --harden

# Or individually
bash hardening/secure-secret-file-permissions.sh
sudo nft -f hardening/nftables-arr-stack.nft
```

### qBittorrent VPN routing

qBittorrent runs behind a gluetun VPN sidecar (`network_mode: "service:gluetun"`). All torrent traffic exits through the WireGuard tunnel. Fill `VPN_SERVICE_PROVIDER`, `WIREGUARD_PRIVATE_KEY`, `WIREGUARD_ADDRESSES`, and `SERVER_COUNTRIES` in `.env` before starting the media stack. Without valid VPN credentials, qBittorrent has no network connectivity.

### Cloudflare Tunnel and ToS

The Cloudflare Tunnel agent (`cloudflared`) provides public HTTPS ingress for Overseerr and select services. Large media streaming through the tunnel may conflict with Cloudflare's Terms of Service. Caching is disabled for media paths; Plex and Jellyfin are accessed directly on the LAN or via Tailscale, not through the tunnel.

### Ollama LAN-only restriction

Ollama binds to `127.0.0.1:11434` on the host (loopback only) and is reachable by other containers via `homelab_net` internal DNS. There is no Caddy ingress label for Ollama, so it is not exposed to the public internet.

### Split-horizon DNS

Public Cloudflare DNS should not contain A records pointing to LAN IPs. The LAN A records for `home.ashorkqueen.xyz` have been removed from Cloudflare. Pi-hole resolves these hostnames locally via `data/pihole/etc-dnsmasq.d/05-homelab-local.conf` (dnsmasq `address=` directive). This keeps all `*.home.ashorkqueen.xyz` subdomains resolvable on the LAN without leaking the private IP publicly.

### Host firewall posture

The nftables ruleset (`hardening/nftables-arr-stack.nft`) filters the host INPUT chain:

- BitTorrent peer ports (`51423`) are open to all sources (required for seeding).
- Management ports (DNS `:53`, HTTP `:80/:443`, Overseerr `:5055`, Pi-hole `:8083`, Jellyfin `:8096`, Plex `:32400`) are restricted to RFC 1918 + Tailscale CGNAT (`100.64.0.0/10`) sources.
- All other traffic to those management ports is dropped.

UFW, if enabled, should be configured to not conflict with Docker's nftables/iptables rules. See [Docker and iptables](https://docs.docker.com/network/iptables/) for details.

### Docker socket proxy

Caddy does not mount `/var/run/docker.sock` directly. Instead, a `docker-socket-proxy` (tecnativa/docker-socket-proxy) exposes a restricted read-only Docker API (containers and networks only, no exec/post) over TCP on `homelab_net`. This limits the blast radius if Caddy is compromised.

### HSTS

All TLS-enabled Caddy sites set `Strict-Transport-Security: max-age=31536000; includeSubDomains` via label. This instructs browsers to only connect over HTTPS for one year.

### Authentication (planned)

Management UIs (Sonarr, Radarr, Prowlarr, Jackett, qBittorrent, Readarr) are currently protected only by their built-in auth and LAN-only Caddy routing. A forward-auth middleware (Authelia or Caddy `basic_auth`) is planned but not yet deployed.

### User namespace remapping (advanced)

For additional container isolation, Docker supports user namespace remapping. Add to `/etc/docker/daemon.json`:

```json
{ "userns-remap": "default" }
```

This maps container root to an unprivileged host UID range. **Test thoroughly** before enabling — it can break volume ownership for containers that run as specific UIDs (Plex, Arr apps with `PUID`/`PGID`). This is a host-level change and is not applied automatically by the setup script.

## License

MIT License. See `LICENSE`.
