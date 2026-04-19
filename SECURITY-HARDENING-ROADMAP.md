# Docker stack — security hardening roadmap

This document extends the stack audit: what was done, what to do next, and long-term direction. API key rotation was deferred per operator choice; tighten file permissions and ingress instead.

## Approved public surface (internet)

**Cloudflare Tunnel hostnames only:**

| Hostname | Service |
|----------|---------|
| `get.ashorkqueen.xyz` | Seerr (`127.0.0.1:5055`) |
| `stream.ashorkqueen.xyz` | Plex (`127.0.0.1:32400`) |
| `jf.ashorkqueen.xyz` | Jellyfin (`127.0.0.1:8096`) |

**Sonarr, Radarr, Jackett, qBittorrent Web UI, FlareSolverr, etc.:** LAN or WireGuard (`../wireguard/`) only — no public DNS/tunnel routes.

## Exposure controls

- `docker-compose.llm.yml` binds internal services to loopback only:
  - Ollama: `127.0.0.1:11434:11434`
  - Internal dashboard: `127.0.0.1:8088:80`
- These loopback binds are intentional to reduce local attack surface while keeping Compose-managed access on the host.

## Done (baseline)

- Tunnel ingress in `/home/jon/docker/data/cloudflared/config.yml` matches the table above (Seerr + Plex stream + Jellyfin). If you use **`CLOUDFLARE_TUNNEL_TOKEN`**, mirror the same routes in the Zero Trust dashboard (local file is ignored in that mode).
- Caddy is optional behind Compose profile `caddy` — do not use it to duplicate tunnel public names; see Caddyfile notes for Seerr.
- Plex `PLEX_CLAIM` removed from compose; reclaim via https://www.plex.tv/claim only when setting up a new server.
- `hardening/secure-secret-file-permissions.sh` — `600` on app secrets; `755` on `cloudflared/credentials/` (not `700`: cloudflared runs as UID `65532` and must **traverse** the directory) and `644` on tunnel `*.json`.
- `hardening/nftables-arr-stack.nft` — optional host firewall snippet: RFC1918 + loopback only to management ports; drops the same ports from WAN.

## Align Cloudflare dashboard (Public Hostnames & DNS)

Local `config.yml` only applies when the tunnel runs with **that file** (`/home/jon/docker/cloudflared/docker-compose.yml`). If you use **`CLOUDFLARE_TUNNEL_TOKEN`** (`arr/docker-compose.cloudflared.yml`), ingress is defined in the **Zero Trust dashboard**, not from `config.yml` — you must edit both places if you switch modes.

### 1. Public Hostnames (Zero Trust)

1. Open [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) and select the correct account/team.
2. Go to **Networks** → **Connectors** → **Cloudflare Tunnels** (older UI labels: **Networks** → **Tunnels**).
3. Open the tunnel that serves this host (dashboard name in your repo: **homelab**; tunnel ID in `config.yml`: `4467aa87-631d-4103-83bd-cbaf8e6c024e` — the row should match one of these).
4. Open the **Public Hostname** (or **Hostname routes**) section for that tunnel.
5. **Keep** exactly:
   - `get.ashorkqueen.xyz` → `http://127.0.0.1:5055`
   - `stream.ashorkqueen.xyz` → `http://127.0.0.1:32400`
   - `jf.ashorkqueen.xyz` → `http://127.0.0.1:8096`
6. **Delete** public routes that violate policy, for example:
   - `qbt.ashorkqueen.xyz` → `:8080`
   - Any other admin UIs not in the table above
7. Save if the UI requires it. Wait a minute for the edge config to update.

### 2. DNS records (zone `ashorkqueen.xyz`)

1. Open the [Cloudflare dashboard](https://dash.cloudflare.com/) → select zone **ashorkqueen.xyz** → **DNS** → **Records**.
2. Ensure **get**, **stream**, and **jf** are **Proxied** CNAMEs (or equivalent) to the tunnel.
3. Remove **qbt** (and similar) records if those hostnames are no longer served.

### 3. Optional: CLI parity later

`cloudflared tunnel list` / route commands need **`cert.pem`** from `cloudflared tunnel login` (account origin certificate). This host currently has no `cert.pem` under `~/.cloudflared`, so the CLI cannot talk to the Cloudflare API for tunnels until you run login once and store the cert next to your credentials. Dashboard edits above do not require that.

## Medium term (weeks)

### Host firewall / UFW

If **UFW** still has `ALLOW Anywhere` on Sonarr/Radarr/qBit/Jackett/FlareSolverr ports (and other non-tunnel services), remove those rules so only LAN/VPN can hit them directly; public access stays via Cloudflare for **get** / **stream** / **jf** only. (Or load `nftables-arr-stack.nft` and avoid conflicting double-filtering.) SSH (22) should be restricted if possible (tailnet, jump host, or allowlist).

### Network isolation

`network_mode: host` is convenient but widens exposure. Migration outline:

1. Create user-defined bridge networks, e.g. `arr_internal`, `edge`.
2. Move services off `host` network; publish only required ports (`ports:`) or keep a single reverse proxy / tunnel container on `host` or `network_mode: host` as the only public listener.
3. Point apps at service names (`qbittorrent:8080`, `flaresolverr:8191`) instead of `127.0.0.1`.
4. Validate qBittorrent, Plex, and *arr connectivity after each change; FlareSolverr may need `extra_hosts` or explicit URLs.

### Authentication and trust

- Enable authentication for *arr and related UIs for all clients (not only non-local), even when they are LAN/VPN-only.
- Seerr: Application URL `https://get.ashorkqueen.xyz`; enable `trustProxy` behind Cloudflare (or your known reverse proxy).

### Supply chain

- Pin image tags to digests or explicit versions; update on a schedule.
- Periodically scan images (`trivy`, `grype`) and host packages.

### Tunnel operations

- Keep `credentials/` at `755` (traverse for UID `65532`) and tunnel JSON at `644`. Avoid syncing credentials to shared or world-readable paths.

## Long term

- **Zero-trust:** Cloudflare Access (or similar) in front of **get**, **stream**, and **jf** with IdP + MFA.
- **Secrets:** Docker secrets, SOPS/age, or Vault for compose and backup automation.
- **Host:** unattended security updates, SSH hardening / fail2ban, immutable backups, log alerting.
- **Network:** isolated VLAN for the server; minimal east-west traffic.

## Validation checklist

- [ ] WAN cannot reach management ports except as intended (test from outside LAN or use an external port scan).
- [ ] Cloudflare tunnel public hostnames are **only** get + stream + jf (unless you deliberately re-approved more).
- [ ] `hardening/secure-secret-file-permissions.sh` run after restores or new configs.
- [ ] Backups of appdata encrypted and stored offsite.
