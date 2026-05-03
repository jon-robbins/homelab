# 3x-UI & Xray Reality VPN Setup

> Deployment, routing, DNS architecture, and client configuration for the VLESS-Reality VPN running alongside the homelab stack.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Container Deployment](#2-container-deployment)
3. [Xray Reality on Port 443](#3-xray-reality-on-port-443)
4. [Caddy Routing for the Web Panel](#4-caddy-routing-for-the-web-panel)
5. [Cloudflare Tunnel for HTTPS Web Services](#5-cloudflare-tunnel-for-https-web-services)
6. [DNS Architecture](#6-dns-architecture)
7. [Share Link Fix (externalProxy)](#7-share-link-fix-externalproxy)
8. [macOS Client Setup](#8-macos-client-setup)

---

## 1. Overview

3x-UI provides a web panel for managing Xray-core, which runs a **VLESS-Reality** inbound on port 443. The setup coexists with the homelab's Caddy reverse proxy and Cloudflare Tunnel by splitting traffic:

- **Port 443** — owned by Xray (Reality protocol, direct IP connections from VPN clients)
- **Port 8443** — owned by Caddy (HTTPS web services, reached via Cloudflare Tunnel)
- **Port 80** — owned by Caddy (HTTP LAN/Tailscale access)

The 3x-UI panel itself is accessible at `https://home.ashorkqueen.xyz/vpn/`.

---

## 2. Container Deployment

### Image & compose location

- **Image:** `ghcr.io/mhsanaei/3x-ui:latest` (upstream stock image from MHSanaei)
- **Compose file:** `homelab/3x-ui/docker-compose.yml` (standalone compose, invoked from inside the homelab repo but not part of the main `docker-compose.yml` stack)

### Key configuration

```yaml
services:
  x-ui:
    image: ghcr.io/mhsanaei/3x-ui:latest
    container_name: 3x-ui
    network_mode: host
    volumes:
      - ./db/:/etc/x-ui/
      - ./certs/:/root/cert/
    environment:
      XRAY_VMESS_AEAD_FORCED: "false"
      XUI_ENABLE_FAIL2BAN: "true"
      XUI_LOG_LEVEL: "info"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:26435/vpn"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

| Setting | Purpose |
|---------|---------|
| `network_mode: host` | Xray binds directly to host ports (443 for Reality, 26435 for panel, 2096 for subs) |
| `./db/` → `/etc/x-ui/` | Persistent database and Xray config |
| `./certs/` → `/root/cert/` | TLS certificates (if needed for non-Reality inbounds) |
| `XUI_ENABLE_FAIL2BAN` | Brute-force protection on the web panel |
| Health check → `/vpn` | Panel's `webBasePath` is set to `/vpn/` |

### Git repo

The `homelab/3x-ui/` directory lives inside the main homelab repo. A local `homelab/3x-ui/.gitignore` excludes runtime data:
- `db/*.db` (sensitive Xray/panel config)
- `certs/` (TLS certificates)
- `.env` (environment secrets)

### Cleanup history

Previous containers (patched, unpatched, custom-built) were removed. The deployment now uses the stock upstream image exclusively — no custom builds or patches.

---

## 3. Xray Reality on Port 443

### Why port 443?

VLESS-Reality works by impersonating a real TLS server (e.g., `www.nvidia.com`). Running on port 443 makes the traffic indistinguishable from normal HTTPS browsing — critical for censorship resistance.

### Port ownership split

| Port | Owner | Purpose |
|------|-------|---------|
| **443** | Xray (via `network_mode: host`) | VLESS-Reality inbound — VPN clients connect here |
| **8443** | Caddy (mapped `8443:443` in compose) | HTTPS web services with Cloudflare DNS-01 TLS |
| **80** | Caddy (mapped `80:80` in compose) | HTTP for LAN / Tailscale / IP-based access |

### How Caddy was freed from port 443

In `docker-compose.network.yml`, Caddy's port mapping was changed:

```yaml
ports:
  - "80:80"
  - "8443:443"                      # HTTPS on 8443 instead of 443
  - "100.106.194.32:443:443"        # Tailscale IP still gets 443
```

- Public port 443 is now free for Xray Reality on the host.
- Caddy still listens on container port 443 internally, but it's mapped to host port 8443.
- The Tailscale IP (`100.106.194.32`) retains a direct 443 binding for convenience.

---

## 4. Caddy Routing for the Web Panel

3x-UI uses **caddy-docker-proxy labels** to register its routes with Caddy. All `/vpn*` paths on `home.ashorkqueen.xyz` are proxied to the panel or subscription server.

### Routing table

| Priority | Path Pattern | Strip prefix? | Backend | Port |
|----------|-------------|---------------|---------|------|
| handle_0 | `/vpn/7HKxYcd1PS*` | Yes (`strip_prefix /vpn`) | Subscription server | 2096 |
| handle_1 | `/vpn/json*` | Yes | Subscription server | 2096 |
| handle_2 | `/vpn/clash*` | Yes | Subscription server | 2096 |
| handle_3 | `/vpn*` | No | Web panel | 26435 |

- **Handles 0–2:** Specific subscription paths → strip `/vpn`, proxy to port 2096. The sub server doesn't expect the `/vpn` prefix.
- **Handle 3:** Catch-all → proxy to port 26435. The panel has `webBasePath: /vpn/` so it expects the prefix intact.
- All handles include `X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Proto` header injection.

### UFW rules required

Since Caddy (bridge network) proxies to host ports via `host.docker.internal`:

```bash
sudo ufw allow from 172.16.0.0/12 to any port 26435 proto tcp   # 3x-UI panel
sudo ufw allow from 172.16.0.0/12 to any port 2096  proto tcp   # 3x-UI subscriptions
```

---

## 5. Cloudflare Tunnel for HTTPS Web Services

With Xray owning port 443, external HTTPS web access uses the **Cloudflare Tunnel** instead of direct connections.

### Tunnel configuration

In `data/cloudflared/config.yml`:

```yaml
ingress:
  # ... existing routes (get., stream., jf.) ...
  - hostname: home.ashorkqueen.xyz
    service: https://127.0.0.1:8443
    originRequest:
      noTLSVerify: true
      originServerName: home.ashorkqueen.xyz
```

| Setting | Purpose |
|---------|---------|
| `service: https://127.0.0.1:8443` | Cloudflared connects to Caddy's HTTPS port on the host |
| `noTLSVerify: true` | Caddy's cert is self-signed from the tunnel's perspective |
| `originServerName` | Tells cloudflared which SNI to send so Caddy selects the correct certificate |

### How it works

```
Browser → Cloudflare Edge → cloudflared (host) → Caddy :8443 → backend service
```

External users hit `https://home.ashorkqueen.xyz` which resolves to Cloudflare (via CNAME), Cloudflare routes through the tunnel to the local cloudflared daemon, which connects to Caddy on port 8443.

---

## 6. DNS Architecture

### Current DNS records

| Record | Type | Points To | Purpose |
|--------|------|-----------|---------|
| `home.ashorkqueen.xyz` | CNAME | `4467aa87-...cfargotunnel.com` | Web services via Cloudflare Tunnel |
| `get.ashorkqueen.xyz` | CNAME | (same tunnel) | Overseerr / Seerr |
| `stream.ashorkqueen.xyz` | CNAME | (same tunnel) | Plex streaming |
| `jf.ashorkqueen.xyz` | CNAME | (same tunnel) | Jellyfin |

### VPN client access — bypasses DNS entirely

Reality VPN clients connect **directly to the server's public IP** (`213.195.110.145:443`). The VLESS URI contains the raw IP address, not the domain name. This means:

- DNS changes don't affect VPN connectivity
- The domain's CNAME to the tunnel is irrelevant for VPN traffic
- VPN traffic hits Xray on port 443; web traffic hits Cloudflare → tunnel → Caddy on 8443

### Key insight

The A record for `home.ashorkqueen.xyz` was removed and replaced with a CNAME to the tunnel. This is safe because VPN clients never resolve the domain — they use the IP directly.

---

## 7. Share Link Fix (externalProxy)

### Problem

3x-UI generates VLESS share links / QR codes based on its inbound configuration. By default, it would generate URIs with the wrong address or SNI, producing broken client configs.

### Solution

Two settings were configured in the 3x-UI panel's inbound configuration:

1. **`externalProxy`** — set to `213.195.110.145:443`
   - Forces generated URIs to use the server's public IP and correct port
   - Without this, URIs might contain `127.0.0.1` or the wrong port

2. **Reality `serverName`** — set to `www.nvidia.com`
   - The SNI (Server Name Indication) sent during the TLS handshake
   - Must match a real, accessible website for the Reality protocol to work
   - This value appears in generated VLESS URIs so clients use the correct SNI

### Result

QR codes and share links from the panel now generate working VLESS URIs like:

```
vless://UUID@213.195.110.145:443?type=tcp&security=reality&sni=www.nvidia.com&fp=chrome&pbk=...&flow=xtls-rprx-vision#name
```

---

## 8. macOS Client Setup

### Installation

Xray core installed via Homebrew:

```bash
brew install xray
# Binary: /opt/homebrew/bin/xray
```

### Client configuration

The client config sets up local proxy inbounds and routes traffic through the Reality tunnel:

| Inbound | Protocol | Listen | Port |
|---------|----------|--------|------|
| SOCKS5 | socks | 127.0.0.1 | 10808 |
| HTTP | http | 127.0.0.1 | 10809 |

### Routing rules

| Traffic | Action |
|---------|--------|
| Chinese domains (`geosite:cn`) | Direct (no tunnel) |
| Chinese IPs (`geoip:cn`) | Direct |
| Private IPs (LAN) | Direct |
| Everything else | Through VLESS-Reality tunnel |

### Shell aliases

```bash
# vpn-on: start xray and set macOS system proxy
vpn-on() {
  brew services start xray
  networksetup -setsocksfirewallproxy Wi-Fi 127.0.0.1 10808
  networksetup -setsocksfirewallproxystate Wi-Fi on
}

# vpn-off: stop xray and disable system proxy
vpn-off() {
  brew services stop xray
  networksetup -setsocksfirewallproxystate Wi-Fi off
}
```

These manage both the Xray service and macOS system proxy settings in one command.

---

## Appendix: File Locations

| File | Purpose |
|------|---------|
| `homelab/3x-ui/docker-compose.yml` | 3x-UI container definition, Caddy labels, health check |
| `homelab/3x-ui/db/` | 3x-UI database and Xray config (gitignored) |
| `homelab/3x-ui/certs/` | TLS certificates (gitignored) |
| `homelab/3x-ui/.gitignore` | Excludes db, certs, .env from version control |
| `homelab/compose/docker-compose.network.yml` | Caddy (port 8443), cloudflared, Tailscale |
| `homelab/data/cloudflared/config.yml` | Tunnel ingress with `home.ashorkqueen.xyz` route |
| `homelab/caddy/Caddyfile` | Primary Caddy routing config (file-based, not label-driven) |

---

*Last updated: May 2026*
