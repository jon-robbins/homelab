# Homelab Security Audit Report

**Date:** 2026-04-25
**Auditor:** Automated (Cursor agent) + live probe results
**Scope:** All three compose stacks (network, media, llm), host exposure, secrets, TLS, container posture
**Runtime reconciled:** All containers recreated with freshly-pulled `:latest` images before probing

---

## Severity Rubric

| Level | Definition |
|---|---|
| **Critical** | Remote unauthenticated access or live secret leak likely |
| **High** | Admin surface reachable from WAN, weak credential, or ToS/abuse risk |
| **Medium** | Container hardening gap, supply-chain risk, defense-in-depth miss |
| **Low** | Hygiene, observability, documentation drift |

---

## Executive Summary

The homelab is reasonably well-architected: bridge networking isolates most services, `no-new-privileges` is broadly applied, `.env` is untracked with mode `600`, and Caddy provides centralized TLS ingress. However, live probing surfaced several actionable findings:

- **Ollama's full model API is exposed unauthenticated via Caddy** at `https://home.ashorkqueen.xyz/ollama/` -- anyone on LAN (or Tailscale) can list, pull, delete, and run models.
- **qBittorrent runs without VPN routing**, exposing the home IP to torrent swarms.
- **Cloudflare tunnel streams Plex/Jellyfin media**, which risks Cloudflare ToS enforcement.
- **Six containers run as root** without `cap_drop: [ALL]`; Pi-hole and Tailscale lack `no-new-privileges`.
- **Every image is `:latest`** with no digest pinning; two workers `pip install` at boot with no version pin.
- The **weak qBittorrent password** `dinosaurpoop` is accepted by the WebUI behind Caddy.

Port 443 is **not forwarded** at the router (confirmed: connection to public IP `213.195.110.145:443` refused), so Caddy-served admin UIs are reachable only from LAN and Tailscale -- not directly from WAN. The three Cloudflare tunnel hostnames are the only true public attack surface.

---

## 1. Host Network Exposure

### Listening on 0.0.0.0 (reachable from LAN and any forwarded path)

| Port | Protocol | Service | Notes |
|---|---|---|---|
| 22 | TCP | SSH | Host sshd |
| 53 | TCP+UDP | Pi-hole DNS | `network_mode: host` |
| 80 | TCP | Caddy HTTP | Redirects to HTTPS |
| 443 | TCP | Caddy HTTPS | Primary ingress |
| 8083 | TCP | Pi-hole admin UI | `network_mode: host`, `FTLCONF_webserver_port` |
| 8096 | TCP | Jellyfin | `network_mode: host` |
| 20241 | TCP | Unknown | Plex-related? |
| 32400 | TCP | Plex Media Server | `network_mode: host` |
| 51423 | TCP+UDP | qBittorrent peer | Published via Docker port mapping |

### Listening on 127.0.0.1 only (good)

| Port | Service |
|---|---|
| 5055 | Overseerr |
| 8088 | internal-dashboard |
| 11434 | Ollama API |
| 18789 | OpenClaw gateway |
| 18790 | OpenClaw bridge |
| 32401/32600 | Plex internal |

### Listening on Tailscale IP only

| Port | Service |
|---|---|
| 100.106.194.32:33504 | Tailscale relay |

### WAN reachability

**Confirmed NOT forwarded:** `curl -4sk --connect-timeout 5 https://213.195.110.145:443` returns connection refused. Port 443 is not NAT-forwarded at the router. Caddy-served UIs are LAN/Tailscale-only.

**Firewall status:** `nft list ruleset` and `iptables -S DOCKER-USER` both require root. The `scripts/hardening/nftables-arr-stack.nft` ruleset is **not auto-loaded** by `setup.sh`.

> **Action required:** Run `sudo nft list ruleset` and paste output to confirm whether the arr_stack_mgmt table is loaded. If not, run:
> ```
> sudo nft -f ./scripts/hardening/nftables-arr-stack.nft
> ```

### Finding: F-1.1 -- Pi-hole admin UI on 0.0.0.0:8083

| | |
|---|---|
| **Severity** | Medium |
| **File** | `compose/docker-compose.network.yml:34` |
| **Impact** | Pi-hole admin panel reachable from any LAN device without Caddy TLS |
| **Recommendation** | Bind to `127.0.0.1:8083` or rely solely on Caddy label routing (`pihole.${BASE_DOMAIN}`) |

---

## 2. Secrets Management

### .env posture

- **Mode:** `600` (owner-only read/write) -- **good**
- **Git tracked:** Never committed (`git log --all -- .env` empty, `.gitignore` has `.env`) -- **good**
- **Backup copies:** None found -- **good**

### Secrets inventory in .env

| Variable | Entropy | Assessment |
|---|---|---|
| `PIHOLE_WEBPASSWORD` | 32-char random | Strong |
| `SONARR_API_KEY` | 32-char hex | Standard Arr-generated |
| `RADARR_API_KEY` | 32-char hex | Standard Arr-generated |
| `READARR_API_KEY` | 32-char hex | Standard Arr-generated |
| `PROWLARR_API_KEY` | 32-char hex | Standard Arr-generated |
| `QBITTORRENT_PASSWORD` | `dinosaurpoop` | **Weak -- dictionary words** |
| `OPENCLAW_GATEWAY_TOKEN` | 48-char hex | Strong |
| `TELEGRAM_BOT_TOKEN` | Standard Telegram format | Adequate |
| `MEDIA_AGENT_TOKEN` | 64-char hex | Strong |
| `GEMINI_API_KEY` | Google format | Adequate |
| `GOOGLE_API_KEY` | Same value as GEMINI_API_KEY | Duplicate -- intentional? |
| `CLOUDFLARE_TOKEN` | CF tunnel token format | Adequate |

### .env vs .env.example drift

Keys in `.env` but **not documented** in `.env.example`: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `MEDIA_AGENT_TOKEN`, `OPENCLAW_GATEWAY_BIND`, `OPENCLAW_GATEWAY_TOKEN`, `PIHOLE_WEBPASSWORD`, `PROWLARR_API_KEY`, `QBITTORRENT_PASSWORD`, `QBITTORRENT_URL`, `QBITTORRENT_USERNAME`, `RADARR_API_KEY`, `RADARR_URL`, `READARR_API_KEY`, `READARR_URL`, `SONARR_API_KEY`, `SONARR_URL`, `TELEGRAM_BOT_TOKEN`.

Keys in `.env.example` but **missing** from `.env`: `CADDY_IMAGE`, `CADDY_INGRESS_NETWORKS`, `CLOUDFLARED_CONFIG_PATH`, `CLOUDFLARED_CONFIG_TEMPLATE`, `DASHY_CONFIG_PATH`, `DASHY_CONFIG_TEMPLATE`, `MEDIA_HDD_PATH`, `MEDIA_NVME_PATH`, `PLEX_DATA_PATH`. (These use defaults from compose so the stack still works.)

### Finding: F-2.1 -- Weak qBittorrent password

| | |
|---|---|
| **Severity** | High |
| **Impact** | `dinosaurpoop` is trivially guessable; qBittorrent WebUI login is exposed via Caddy at `${BASE_DOMAIN}/qbittorrent/` |
| **Recommendation** | Rotate to a 20+ char random password. Update `.env` and qBittorrent WebUI settings |

### Finding: F-2.2 -- qBittorrent.conf world-readable

| | |
|---|---|
| **Severity** | Medium |
| **File** | `data/qbittorrent/config/qBittorrent/qBittorrent.conf` (mode `644`) |
| **Impact** | Contains hashed WebUI password; readable by any user on the host |
| **Recommendation** | Add to `scripts/hardening/secure-secret-file-permissions.sh` paths array and `chmod 600` |

### Finding: F-2.3 -- .env.example significantly out of sync

| | |
|---|---|
| **Severity** | Low |
| **Impact** | New users cannot discover required secret variables from `.env.example` alone |
| **Recommendation** | Add all secret variable stubs (blank defaults) to `.env.example` with comments |

---

## 3. WAN / Ingress Posture

### DNS resolution (from 1.1.1.1)

| Hostname | Resolves to | Via |
|---|---|---|
| `home.ashorkqueen.xyz` | `192.168.1.184` (LAN) | Direct A record |
| `plex.home.ashorkqueen.xyz` | `192.168.1.184` (LAN) | Direct A record |
| `jellyfin.home.ashorkqueen.xyz` | `192.168.1.184` (LAN) | Direct A record |
| `pihole.home.ashorkqueen.xyz` | `192.168.1.184` (LAN) | Direct A record |
| `openclaw.home.ashorkqueen.xyz` | `192.168.1.184` (LAN) | Direct A record |
| `get.ashorkqueen.xyz` | `104.21.51.78` / `172.67.177.98` | Cloudflare proxy |
| `stream.ashorkqueen.xyz` | `104.21.51.78` / `172.67.177.98` | Cloudflare proxy |
| `jf.ashorkqueen.xyz` | `104.21.51.78` / `172.67.177.98` | Cloudflare proxy |

**Assessment:** `home.ashorkqueen.xyz` and its subdomains resolve to the LAN IP `192.168.1.184` in public DNS. Since port 443 is not forwarded, this is only reachable on LAN. However, any DNS-based scanner can discover the private IP and the existence of this infrastructure.

### Cloudflare tunnel routes (`data/cloudflared/config.yml`)

| Public hostname | Backend | Probe result |
|---|---|---|
| `get.ashorkqueen.xyz` | `http://127.0.0.1:5055` (Overseerr) | `307 /login` -- auth wall present |
| `stream.ashorkqueen.xyz` | `http://127.0.0.1:32400` (Plex) | `401` -- auth required |
| `jf.ashorkqueen.xyz` | `http://127.0.0.1:8096` (Jellyfin) | `302 /web/` -- login page |

### Finding: F-3.1 -- Plex/Jellyfin streaming via Cloudflare tunnel

| | |
|---|---|
| **Severity** | High |
| **File** | `data/cloudflared/config.yml:14-18` |
| **Impact** | Cloudflare's Self-Serve Subscription Agreement (section 2.8) prohibits serving disproportionate non-HTML content through their network. Streaming video through a tunnel may trigger enforcement |
| **Recommendation** | Remove `stream.ashorkqueen.xyz` and `jf.ashorkqueen.xyz` from the tunnel. Use Tailscale for remote media access, or a direct VPN |

### Finding: F-3.2 -- Private IP leaked in public DNS

| | |
|---|---|
| **Severity** | Low |
| **File** | Cloudflare DNS zone (external) |
| **Impact** | `192.168.1.184` is visible to anyone querying DNS. Reveals internal network topology |
| **Recommendation** | Use split-horizon DNS: set `home.ashorkqueen.xyz` to Cloudflare-proxied or remove the public A record and rely on Pi-hole / Tailscale DNS for LAN resolution |

---

## 4. Container Privilege and Isolation

### Security posture per container

| Container | User | network_mode | no-new-privileges | cap_add | cap_drop |
|---|---|---|---|---|---|
| caddy | (root) | bridge | **yes** | -- | -- |
| pihole | root | **host** | **NO** | NET_BIND_SERVICE, NET_RAW | -- |
| dashy | (root) | bridge | **yes** | -- | -- |
| cloudflared | 65532:65532 | **host** | **yes** | -- | -- |
| tailscale | (root) | **host** | **NO** | NET_ADMIN, NET_RAW | -- |
| flaresolverr | flaresolverr | bridge | **yes** | -- | -- |
| prowlarr | (root) | bridge | **yes** | -- | -- |
| sonarr | (root) | bridge | **yes** | -- | -- |
| radarr | (root) | bridge | **yes** | -- | -- |
| readarr | (root) | bridge | **yes** | -- | -- |
| overseerr | node:node | bridge | **yes** | -- | -- |
| plex | (root) | **host** | **yes** | -- | -- |
| jellyfin | (root) | **host** | **yes** | -- | -- |
| qbittorrent | (root) | bridge | **yes** | -- | -- |
| media-agent | **root** | bridge | **yes** | -- | -- |
| ollama | **root** | bridge | **yes** | -- | -- |
| internal-dashboard | **root** | bridge | **yes** | -- | -- |
| openclaw-gateway | node | bridge | **yes** | -- | -- |

### Finding: F-4.1 -- Pi-hole and Tailscale missing no-new-privileges

| | |
|---|---|
| **Severity** | Medium |
| **Files** | `compose/docker-compose.network.yml:22-49` (pihole), `compose/docker-compose.network.yml:85-102` (tailscale) |
| **Impact** | Both run as root on `network_mode: host`. Without `no-new-privileges`, a vulnerability in either container could escalate to full host root |
| **Recommendation** | Add `security_opt: ["no-new-privileges:true"]` to both services |

### Finding: F-4.2 -- Pi-hole has unnecessary NET_RAW capability

| | |
|---|---|
| **Severity** | Low |
| **File** | `compose/docker-compose.network.yml:41` |
| **Impact** | `NET_RAW` allows raw socket creation; only needed if Pi-hole acts as DHCP server |
| **Recommendation** | Remove `NET_RAW` from `cap_add` unless DHCP mode is enabled |

### Finding: F-4.3 -- Docker socket mounted in Caddy

| | |
|---|---|
| **Severity** | Medium |
| **File** | `compose/docker-compose.network.yml:11` |
| **Impact** | `/var/run/docker.sock:ro` gives Caddy read access to the Docker API. If Caddy is compromised, an attacker can enumerate all containers, env vars (including secrets), and potentially escape to the host |
| **Recommendation** | Interpose `tecnativa/docker-socket-proxy` to filter API calls to only the endpoints caddy-docker-proxy needs |

### Finding: F-4.4 -- cloudflared has loopback access to all 127.0.0.1 services

| | |
|---|---|
| **Severity** | Medium |
| **File** | `compose/docker-compose.network.yml:74-83` |
| **Impact** | `network_mode: host` means cloudflared can reach Overseerr (`:5055`), Ollama (`:11434`), OpenClaw (`:18789`), internal-dashboard (`:8088`), and Plex admin (`:32401`) on localhost. If the tunnel agent is compromised via a crafted tunnel config, all loopback services are accessible |
| **Recommendation** | Move cloudflared to bridge networking with explicit upstream targets, or restrict its tunnel ingress rules to only the services it should expose |

### Finding: F-4.5 -- No cap_drop: [ALL] baseline

| | |
|---|---|
| **Severity** | Medium |
| **Impact** | No container declares `cap_drop: [ALL]` + explicit `cap_add`. Containers inherit Docker's default capability set (~14 capabilities including `CHOWN`, `DAC_OVERRIDE`, `SETUID`, `SETGID`) |
| **Recommendation** | Add `cap_drop: [ALL]` as a baseline to all bridge-mode services, then `cap_add` only what each needs |

### Finding: F-4.6 -- Multiple containers run as root

| | |
|---|---|
| **Severity** | Medium |
| **Impact** | `media-agent`, `ollama`, `internal-dashboard`, and `pihole` all confirmed running as UID 0. A container breakout from any of these grants host root |
| **Recommendation** | For `python:3.12-alpine` workers: add `USER nobody` or create a dedicated user. For `media-agent`: add `USER` in Dockerfile. For `ollama`: set `user: "1000:1000"` in compose. For `internal-dashboard` (nginx): use `nginxinc/nginx-unprivileged` |

---

## 5. Torrent / VPN Policy

### Finding: F-5.1 -- qBittorrent not routed through VPN

| | |
|---|---|
| **Severity** | High |
| **File** | `compose/docker-compose.media.yml:198-225` |
| **Impact** | qBittorrent peer port `51423/tcp+udp` is published on the host's public-facing interface. The home IP address (`213.195.110.145`) is visible to every torrent swarm. This contradicts the `secure-homelab-architect` skill rule requiring `network_mode: "container:wireguard"` |
| **Recommendation** | Add a `gluetun` or `wireguard` sidecar container, route qBittorrent through it with `network_mode: "service:gluetun"`, and remove the host port publishing |

---

## 6. Image and Supply-Chain Hygiene

### Finding: F-6.1 -- All images use :latest or unpinned tags

| | |
|---|---|
| **Severity** | Medium |
| **Impact** | Every image (21 containers) uses `:latest` or an unpinned nightly tag (`readarr:0.4.19-nightly`). A compromised upstream image or breaking change is automatically deployed on every `docker compose pull && up -d` |
| **Recommendation** | Pin images to specific version tags or SHA256 digests. Use Renovate or Dependabot to automate updates with review |

### Finding: F-6.2 -- Runtime pip install with no version pin

| | |
|---|---|
| **Severity** | Medium |
| **Files** | `compose/docker-compose.media.yml` |
| **Impact** | `pip install --quiet httpx` runs at every container start with no version pin and no hash verification. A PyPI supply-chain attack would be automatically deployed |
| **Recommendation** | Build a small Dockerfile from `python:3.12-alpine` that bakes in `httpx` with pinned version. The workspace already has `src/homelab_workers/pyproject.toml` with version constraints |

---

## 7. Application Authentication

### L7 probe results -- Caddy ingress paths (from LAN)

| Path | HTTP Status | Auth Required? | Notes |
|---|---|---|---|
| `/sonarr/` | 401 | Yes (API key / forms) | Sonarr blocks unauthenticated access |
| `/radarr/` | 401 | Yes | Same as Sonarr |
| `/readarr/` | 401 | Yes | Same as Sonarr |
| `/prowlarr/` | 401 | Yes | Same as Sonarr |
| `/qbittorrent/` | **200** | **Login page served** | Login page accessible; weak password `dinosaurpoop` |
| `/flaresolverr/` | **200** | **No** | Returns JSON status; no auth at all |
| `/overseerr/` | 307 -> `/login` | Yes (first-party auth) | Good |
| `/ollama/` | **200** | **No** | **Full API exposed: model list, pull, generate, delete** |
| `/dashboard/` | **200** | **No** | Static HTML dashboard; informational only |

### Finding: F-7.1 -- Ollama API fully exposed without authentication

| | |
|---|---|
| **Severity** | High |
| **File** | `compose/docker-compose.llm.yml:22-24` (Caddy labels) |
| **Impact** | `https://home.ashorkqueen.xyz/ollama/api/tags` returns complete model inventory. `POST /ollama/api/generate` allows arbitrary inference. `DELETE /ollama/api/delete` can remove models. Any device on LAN or Tailscale can abuse GPU resources or exfiltrate model weights |
| **Recommendation** | Either (a) remove the Caddy label and access Ollama only via the `127.0.0.1:11434` loopback binding, or (b) add Caddy `basic_auth` or `forward_auth` (Authelia/Authentik) in front of the `/ollama*` path |

### Finding: F-7.2 -- FlareSolverr exposed without authentication

| | |
|---|---|
| **Severity** | Medium |
| **File** | `compose/docker-compose.media.yml:12-16` (Caddy labels) |
| **Impact** | FlareSolverr solves CAPTCHAs via headless Chrome. Exposing it on the Caddy ingress allows any LAN device to use it as a CAPTCHA-solving proxy |
| **Recommendation** | FlareSolverr only needs to be reachable from Prowlarr on the internal `homelab_net` network |

### Finding: F-7.3 -- No global auth layer on Caddy

| | |
|---|---|
| **Severity** | Medium |
| **Impact** | Each service relies on its own authentication. There is no SSO or reverse-proxy-level auth. If any service has an auth bypass vulnerability, it's directly exploitable |
| **Recommendation** | Add Authelia or Authentik as a `forward_auth` middleware on Caddy for all management paths. Exempt Overseerr and media streaming endpoints that have their own auth |

---

## 8. Volume and Data Exposure

### Finding: F-8.1 -- Plex has read-write access to entire media trees

| | |
|---|---|
| **Severity** | Low |
| **File** | `compose/docker-compose.media.yml:153-159` |
| **Impact** | Plex mounts `/mnt/media-hdd` and `/mnt/media-nvme` without `:ro`. A compromised Plex instance could modify or delete the entire media library. Jellyfin correctly uses `:ro` |
| **Recommendation** | Add `:ro` to Plex library mounts. Use a separate writable path for transcoding temp files |

---

## 9. Logging, Healthchecks, and Resource Limits

### Healthcheck coverage

| Has healthcheck | Missing healthcheck |
|---|---|
| pihole, overseerr, openclaw-gateway, media-agent | caddy, sonarr, radarr, readarr, prowlarr, qbittorrent, plex, jellyfin, ollama, flaresolverr, internal-dashboard, dashy, tailscale, cloudflared |

### Finding: F-9.1 -- Most services lack healthchecks

| | |
|---|---|
| **Severity** | Low |
| **Impact** | Docker cannot detect and restart unhealthy containers. Sonarr, Radarr, Prowlarr all expose `/ping` endpoints but no healthcheck is defined |
| **Recommendation** | Add healthchecks for at least Sonarr/Radarr/Prowlarr (`curl -f http://localhost:<port>/<base>/ping`) and Caddy (`curl -f http://localhost:2019/reverse_proxy/upstreams` or similar) |

### Finding: F-9.2 -- No logging driver limits

| | |
|---|---|
| **Severity** | Low |
| **Impact** | No `logging.driver` or `logging.options.max-size` is set on any container. A runaway log can fill `/var/lib/docker` and crash the host |
| **Recommendation** | Set `logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }` globally or per-service |

### Finding: F-9.3 -- No resource limits beyond qBittorrent

| | |
|---|---|
| **Severity** | Low |
| **Impact** | Only qBittorrent has `mem_limit: 4g`. Ollama (GPU workload), Plex (transcode), and FlareSolverr (headless Chrome with `shm_size: 2gb`) have no memory or PID limits |
| **Recommendation** | Add `mem_limit` and `pids_limit` to at least Ollama, Plex, and FlareSolverr |

---

## 10. Operational Hardening Drift

### Finding: F-10.1 -- setup.sh does not apply hardening

| | |
|---|---|
| **Severity** | Low |
| **Files** | `scripts/setup.sh`, `scripts/hardening/secure-secret-file-permissions.sh`, `scripts/hardening/nftables-arr-stack.nft` |
| **Impact** | `setup.sh` creates `.env`, copies templates, creates Docker network, and validates compose -- but does not run the permission hardening script or load nftables. Users must remember to run these separately |
| **Recommendation** | Add an `--harden` flag to `setup.sh` that optionally runs `scripts/hardening/secure-secret-file-permissions.sh` and loads the nftables ruleset |

### Finding: F-10.2 -- README Security section understates risk

| | |
|---|---|
| **Severity** | Low |
| **File** | `README.md:327-330` |
| **Impact** | The Security section does not mention the qBittorrent VPN gap, Cloudflare ToS concern for media streaming, Ollama's unauthenticated API exposure, or the missing `no-new-privileges` on Pi-hole/Tailscale |
| **Recommendation** | Expand the Security section to document known risks and their mitigations |

---

## 11. TLS / Certificate Findings

### Certificate inventory

| Hostname | Issuer | Valid From | Valid To | Key Type | SAN |
|---|---|---|---|---|---|
| `home.ashorkqueen.xyz` | Let's Encrypt E8 | 2026-04-24 | 2026-07-23 | ECDSA | `home.ashorkqueen.xyz` |
| `plex.home.ashorkqueen.xyz` | Let's Encrypt E8 | 2026-04-24 | 2026-07-23 | ECDSA | `plex.home.ashorkqueen.xyz` |
| `jellyfin.home.ashorkqueen.xyz` | Let's Encrypt E8 | 2026-04-24 | 2026-07-23 | ECDSA | (assumed same pattern) |
| `pihole.home.ashorkqueen.xyz` | Let's Encrypt E8 | 2026-04-24 | 2026-07-23 | ECDSA | (assumed same pattern) |
| `openclaw.home.ashorkqueen.xyz` | Let's Encrypt E8 | 2026-04-24 | 2026-07-23 | ECDSA | (assumed same pattern) |
| `get.ashorkqueen.xyz` (tunnel) | Google Trust WE1 | 2026-03-24 | 2026-06-22 | ECDSA | `*.ashorkqueen.xyz`, `ashorkqueen.xyz` |

### TLS protocol support

| Test | Result |
|---|---|
| TLS 1.3 | **Supported** (TLS_AES_128_GCM_SHA256) |
| TLS 1.0 | **Refused** ("no protocols available") |

### Finding: F-11.1 -- No HSTS header

| | |
|---|---|
| **Severity** | Medium |
| **Impact** | Caddy does not send `Strict-Transport-Security` header. Clients may fall back to HTTP on first visit or after cache expiry, enabling MITM downgrade attacks on LAN |
| **Recommendation** | Add HSTS via a global Caddy snippet or per-site `header` directive: `Strict-Transport-Security "max-age=31536000; includeSubDomains"` |

### Positive findings

- ECDSA certificates via Let's Encrypt ACME DNS-01 challenge (Cloudflare) -- strong
- TLS 1.0/1.1 correctly refused
- Caddy ACME storage at `data/caddy/data/caddy/` is mode `700` (owner-only) -- good
- Cloudflare tunnel uses separate Google Trust-issued wildcard cert for `*.ashorkqueen.xyz`

---

## 12. Live Probe Results Matrix

### Docker daemon posture

| Setting | Value | Assessment |
|---|---|---|
| Storage driver | overlayfs | Standard |
| Cgroup driver | systemd | Recommended |
| Cgroup version | 2 | Modern, supports full resource control |
| Security options | apparmor, seccomp (builtin), cgroupns | Good baseline |
| Live restore | **false** | Containers stop on daemon restart |
| User namespace remap | **none** | Root in container = root on host |
| Default runtime | nvidia | GPU support enabled |

### Finding: F-12.1 -- No user namespace remapping

| | |
|---|---|
| **Severity** | Medium |
| **Impact** | Docker `userns-remap` is not configured. UID 0 inside containers maps to UID 0 on the host. Combined with the 6 root-running containers, a container escape grants host root |
| **Recommendation** | Enable `userns-remap` in `/etc/docker/daemon.json` (requires testing all containers for compatibility) |

### Behavioral test results

| Test | Result | Assessment |
|---|---|---|
| Sonarr API (`/api/v3/system/status`) | v4.0.17.2952, auth=forms | API key valid, forms auth enabled |
| Radarr API (`/api/v3/system/status`) | v6.1.1.10360, auth=forms | API key valid, forms auth enabled |
| qBittorrent login | `200 OK`, `Set-Cookie: SID=...` | **Weak password accepted, session issued** |
| Ollama `/api/tags` | Full model list (6 models, ~60GB total) | **No auth, full API exposed** |
| Overseerr public settings | `initialized=true`, login redirects | Auth wall present |
| OpenClaw `/healthz` | Empty response (still starting) | Eventual healthy state confirmed |
| media-agent health (no auth) | Empty response (no host port published) | Not directly reachable from host; only via `homelab_net` |

### Tunnel hostname probes (from host, via Cloudflare)

| Hostname | Status | Auth | Notes |
|---|---|---|---|
| `get.ashorkqueen.xyz` | 307 -> `/login` | Yes | Overseerr login page |
| `stream.ashorkqueen.xyz` | 401 | Yes | Plex requires auth |
| `jf.ashorkqueen.xyz` | 302 -> `/web/` | Yes | Jellyfin login page |

---

## Severity Summary

### Critical

No critical findings. Port 443 is not forwarded, so Caddy-served admin UIs are not WAN-reachable. The three tunnel-published services all have authentication.

### High (4 findings)

| ID | Finding | Remediation Priority |
|---|---|---|
| F-2.1 | Weak qBittorrent password (`dinosaurpoop`) | Immediate |
| F-5.1 | qBittorrent not routed through VPN (home IP exposed to swarms) | Immediate |
| F-3.1 | Plex/Jellyfin streaming via Cloudflare tunnel (ToS risk) | Soon |
| F-7.1 | Ollama API fully exposed without authentication via Caddy | Soon |

### Medium (13 findings)

| ID | Finding |
|---|---|
| F-1.1 | Pi-hole admin UI on 0.0.0.0:8083 |
| F-2.2 | qBittorrent.conf world-readable (mode 644) |
| F-4.1 | Pi-hole and Tailscale missing no-new-privileges |
| F-4.3 | Docker socket mounted in Caddy |
| F-4.4 | cloudflared has loopback access to all 127.0.0.1 services |
| F-4.5 | No cap_drop: [ALL] baseline |
| F-4.6 | Multiple containers run as root |
| F-6.1 | All images use :latest or unpinned tags |
| F-6.2 | Runtime pip install with no version pin |
| F-7.2 | FlareSolverr exposed without authentication via Caddy |
| F-7.3 | No global auth layer on Caddy |
| F-11.1 | No HSTS header |
| F-12.1 | No user namespace remapping |

### Low (9 findings)

| ID | Finding |
|---|---|
| F-2.3 | .env.example significantly out of sync |
| F-3.2 | Private IP leaked in public DNS |
| F-4.2 | Pi-hole has unnecessary NET_RAW capability |
| F-8.1 | Plex has read-write access to entire media trees |
| F-9.1 | Most services lack healthchecks |
| F-9.2 | No logging driver limits |
| F-9.3 | No resource limits beyond qBittorrent |
| F-10.1 | setup.sh does not apply hardening |
| F-10.2 | README Security section understates risk |

---

## Recommended Remediation Order

1. **Rotate qBittorrent password** to 20+ char random (F-2.1)
2. **Add VPN sidecar** (gluetun) for qBittorrent and remove host port publish (F-5.1)
3. **Remove Ollama Caddy label** or add auth middleware (F-7.1)
4. **Remove FlareSolverr Caddy label** (F-7.2)
5. **Remove Plex/Jellyfin from Cloudflare tunnel**; use Tailscale for remote access (F-3.1)
6. **Add `security_opt: ["no-new-privileges:true"]`** to pihole and tailscale (F-4.1)
7. **Add `cap_drop: [ALL]`** baseline to all bridge services (F-4.5)
8. **Add HSTS header** to Caddy global config (F-11.1)
9. **Pin image versions** and set up Renovate/Dependabot (F-6.1)
10. **Build worker Dockerfile** to eliminate runtime pip install (F-6.2)

---

## Manual Verification Required

The following check requires root access and could not be completed during this audit:

```bash
sudo nft list ruleset
```

Paste the output to confirm whether `table inet arr_stack_mgmt` from `scripts/hardening/nftables-arr-stack.nft` is currently loaded. If not:

```bash
sudo nft -f ./scripts/hardening/nftables-arr-stack.nft
sudo nft list table inet arr_stack_mgmt
```
