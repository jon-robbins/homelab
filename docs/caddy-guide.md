# Caddy Reverse Proxy Guide — Homelab Setup

> Practical reference for how Caddy works in this homelab, common pitfalls, and solutions.

---

## Table of Contents

1. [Overview](#1-overview)
2. [How Caddy Docker Proxy Works](#2-how-caddy-docker-proxy-works)
3. [Routing for Host-Network Services](#3-routing-for-host-network-services)
4. [Common Struggles & Solutions](#4-common-struggles--solutions)
5. [Label Reference Quick Sheet](#5-label-reference-quick-sheet)
6. [Potential Future Upgrades](#6-potential-future-upgrades)

---

## 1. Overview

This homelab runs **[caddy-docker-proxy](https://github.com/lucaslorentz/caddy-docker-proxy)**, NOT a standard Caddy server with a hand-written Caddyfile. Key points:

- **Config is generated automatically** from Docker container labels — you almost never edit a Caddyfile directly.
- A static Caddyfile exists at `data/caddy/Caddyfile` as a **reference/fallback** (e.g. for the LAN-only `:80` routes). It is not the primary config source for HTTPS routes.
- Caddy listens on **port 80** (HTTP) and **port 8443** (HTTPS, mapped from container port 443) on the host. Port 443 on the public interface is reserved for Xray Reality (see `3x-ui-vpn-setup.md`). The Tailscale IP (`100.106.194.32`) retains a direct `:443` binding.
- TLS certificates are provisioned automatically via **Cloudflare DNS-01 challenge** (no port-80 ACME needed).
- A **Docker socket proxy** (`tecnativa/docker-socket-proxy`) sits between Caddy and the Docker socket for security — Caddy never mounts `/var/run/docker.sock` directly.

> **Note (May 2026):** The primary routing config has migrated from label-only (caddy-docker-proxy) to a **file-based Caddyfile** mounted at `/etc/caddy/Caddyfile`. Most routes are defined in `homelab/caddy/Caddyfile`. The 3x-UI service (separate compose project) still uses caddy-docker-proxy labels for its `/vpn*` routes.

### Container definition (from `docker-compose.network.yml`)

```yaml
caddy:
  image: ${CADDY_IMAGE:-local/caddy-cf:latest}
  ports:
    - "80:80"
    - "8443:443"                      # HTTPS on 8443; public 443 reserved for Xray Reality
    - "100.106.194.32:443:443"        # Tailscale IP keeps direct 443
  volumes:
    - ../caddy/Caddyfile:/etc/caddy/Caddyfile:ro
    - ../data/caddy/data:/data
    - ../data/caddy/config:/config
  environment:
    - BASE_DOMAIN=${BASE_DOMAIN}
    - CLOUDFLARE_TOKEN=${CLOUDFLARE_TOKEN}
  networks:
    - homelab_net
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

Key things to note:

| Setting | Purpose |
|---------|---------|
| `8443:443` | Maps Caddy's internal HTTPS (443) to host port 8443 — public 443 is for Xray Reality |
| `100.106.194.32:443:443` | Tailscale IP retains direct 443 binding for local HTTPS |
| `Caddyfile:/etc/caddy/Caddyfile:ro` | Mounts the file-based routing config (read-only) |
| `BASE_DOMAIN` | Domain used in Caddyfile site addresses |
| `CLOUDFLARE_TOKEN` | API token for DNS-01 TLS challenge |
| `extra_hosts` → `host.docker.internal` | Allows Caddy to reach services running in `network_mode: host` |

---

## 2. How Caddy Docker Proxy Works

### Label → Route generation

Any container with `caddy.*` labels is picked up by caddy-docker-proxy and turned into Caddy config directives at runtime. The label key maps to Caddy directives; the label value is the directive argument.

### Basic label anatomy

```
caddy: <site-address>          # Which domain/port this block applies to
caddy.<directive>: <value>     # A Caddy directive inside that site block
```

### Upstream resolution

- **Bridge-network containers** — use the magic `{{upstreams <port>}}` placeholder. Caddy resolves it to the container's IP on the ingress network.
- **Host-network containers** — can't use `{{upstreams}}`; use `host.docker.internal:<port>` instead.

### Ordered handles (the `_N` prefix)

When you need multiple `handle` blocks under the same site address, caddy-docker-proxy uses a **numeric prefix** to set ordering:

```
caddy.handle_0: "/specific/path*"   # processed first (highest priority)
caddy.handle_1: "/other/path*"
caddy.handle_3: "/fallback*"        # processed last (lowest priority)
```

Within a handle block, directives are also ordered:

```
caddy.handle_0.0_uri: "strip_prefix /vpn"      # first directive inside handle_0
caddy.handle_0.1_reverse_proxy: "backend:8080"  # second directive
```

### TLS via labels

```yaml
labels:
  caddy.tls.dns: "cloudflare {env.CLOUDFLARE_TOKEN}"
```

This tells Caddy to use the Cloudflare DNS-01 challenge for certificate provisioning. The `{env.CLOUDFLARE_TOKEN}` is resolved from the **Caddy container's** environment, not the labeled container's.

### HSTS header

```yaml
labels:
  caddy.header.Strict-Transport-Security: "max-age=31536000; includeSubDomains"
```

---

## 3. Routing for Host-Network Services

### The problem

Containers using `network_mode: host` **cannot join Docker bridge networks**. They share the host's network stack, so they're invisible to Caddy's Docker-network-based upstream resolution.

### The pattern

1. The labeled container sets `network_mode: host`.
2. Labels use `host.docker.internal:<port>` as the proxy target instead of `{{upstreams}}`.
3. The **Caddy container** must have `extra_hosts: ["host.docker.internal:host-gateway"]` so it can resolve that hostname.

### Real example — 3x-UI (from `/home/jon/3x-ui/docker-compose.yml`)

3x-UI runs two backends on the host:
- **Web panel** on port `26435`
- **Subscription server** on port `2096`

Both are exposed under `https://home.ashorkqueen.xyz/vpn/`:

```yaml
services:
  x-ui:
    image: ghcr.io/mhsanaei/3x-ui:latest
    network_mode: host
    labels:
      caddy: home.ashorkqueen.xyz

      # --- Subscription paths (strip /vpn, proxy to sub server) ---
      caddy.handle_0: "/vpn/7HKxYcd1PS*"
      caddy.handle_0.0_uri: "strip_prefix /vpn"
      caddy.handle_0.1_reverse_proxy: "host.docker.internal:2096"

      caddy.handle_1: "/vpn/json*"
      caddy.handle_1.0_uri: "strip_prefix /vpn"
      caddy.handle_1.1_reverse_proxy: "host.docker.internal:2096"

      caddy.handle_2: "/vpn/clash*"
      caddy.handle_2.0_uri: "strip_prefix /vpn"
      caddy.handle_2.1_reverse_proxy: "host.docker.internal:2096"

      # --- Web panel catch-all ---
      caddy.handle_3: "/vpn*"
      caddy.handle_3.0_reverse_proxy: "host.docker.internal:26435"

      caddy.tls.dns: "cloudflare {env.CLOUDFLARE_TOKEN}"
      caddy.header.Strict-Transport-Security: "max-age=31536000; includeSubDomains"
```

**Why this ordering matters:** Handles `0–2` match specific sub-paths under `/vpn` and strip the prefix before proxying to the subscription server (port 2096). Handle `3` is the catch-all that sends everything else under `/vpn*` to the web panel (port 26435), which has its `webBasePath` set to `/vpn/` so it expects the prefix.

### Other host-network examples

**Plex** (`docker-compose.media.yml`):
```yaml
labels:
  caddy: "plex.${BASE_DOMAIN}"
  caddy.reverse_proxy: "host.docker.internal:32400"
  caddy.tls.dns: cloudflare {env.CLOUDFLARE_TOKEN}
```

**Jellyfin** (`docker-compose.media.yml`):
```yaml
labels:
  caddy: "jellyfin.${BASE_DOMAIN}"
  caddy.reverse_proxy: "host.docker.internal:8096"
  caddy.tls.dns: cloudflare {env.CLOUDFLARE_TOKEN}
```

---

## 4. Common Struggles & Solutions

### a. UFW blocking container-to-host traffic

**Problem:** Caddy container can't reach services listening on the host because UFW blocks Docker bridge traffic from the `172.16.0.0/12` range.

**Symptom:** `502 Bad Gateway` from Caddy, even though the service works fine on `localhost`.

**Solution:**
```bash
sudo ufw allow from 172.16.0.0/12 to any port <PORT> proto tcp
```

You need one rule **per host port** that Caddy proxies to. For example:
```bash
sudo ufw allow from 172.16.0.0/12 to any port 26435 proto tcp   # 3x-UI panel
sudo ufw allow from 172.16.0.0/12 to any port 2096  proto tcp   # 3x-UI subscriptions
sudo ufw allow from 172.16.0.0/12 to any port 32400 proto tcp   # Plex
sudo ufw allow from 172.16.0.0/12 to any port 8096  proto tcp   # Jellyfin
```

**Why:** Docker containers on a bridge network get IPs in `172.x.x.x`. When Caddy (in the bridge) tries to connect to `host.docker.internal`, the traffic appears to come from the Docker bridge subnet. UFW's default-deny policy blocks it.

---

### b. `handle_path` vs `handle` — path stripping

| Directive | Strips prefix? | Use when... |
|-----------|---------------|-------------|
| `handle_path /prefix*` | **Yes** — strips `/prefix` before proxying | Backend does NOT expect the prefix in the URL |
| `handle /prefix*` | **No** — passes the full path through | Backend expects the full path including prefix |

**Examples from this setup:**

- **Prowlarr** uses `handle_path` — Prowlarr doesn't expect `/prowlarr` in the path:
  ```yaml
  caddy.handle_path: "/prowlarr*"
  caddy.handle_path.0_reverse_proxy: "{{upstreams 9696}}"
  ```

- **Sonarr** uses `handle` — Sonarr is configured with URL base `/sonarr`:
  ```yaml
  caddy.handle: "/sonarr*"
  caddy.handle.0_reverse_proxy: "{{upstreams 8989}}"
  ```

- **3x-UI panel** uses `handle` (catch-all) — panel has `webBasePath: /vpn/`:
  ```yaml
  caddy.handle_3: "/vpn*"
  caddy.handle_3.0_reverse_proxy: "host.docker.internal:26435"
  ```

- **3x-UI subscriptions** use `uri strip_prefix` inside a `handle` — sub server doesn't expect `/vpn`:
  ```yaml
  caddy.handle_0: "/vpn/json*"
  caddy.handle_0.0_uri: "strip_prefix /vpn"
  caddy.handle_0.1_reverse_proxy: "host.docker.internal:2096"
  ```

> **Tip:** If you're unsure, check whether the backend app has a "base URL" or "path prefix" setting. If it does and it's set, use `handle`. If it doesn't, use `handle_path`.

---

### c. Multiple backends on different ports under the same path prefix

**Problem:** 3x-UI has a web panel on port 26435 and a subscription server on port 2096, both needing to live under `/vpn`.

**Solution:** Use ordered handles with numeric prefixes for priority:

```
handle_0  →  /vpn/7HKxYcd1PS*  →  port 2096 (sub path, specific)
handle_1  →  /vpn/json*        →  port 2096 (sub path, specific)
handle_2  →  /vpn/clash*       →  port 2096 (sub path, specific)
handle_3  →  /vpn*             →  port 26435 (catch-all, least specific)
```

**Key rule:** More specific paths get **lower numbers** (higher priority). The catch-all gets the **highest number** (lowest priority). Caddy processes them in order and the first match wins.

---

### d. Service not appearing in Caddy routes

Checklist:

1. **Container is on the `homelab_net` network** (or whichever `CADDY_INGRESS_NETWORKS` is set to). Host-network containers are the exception — they don't join any Docker network but still get picked up via labels.
2. **Labels are syntactically correct** — a single typo silently drops the route.
3. **The `caddy:` label matches a valid site address** — usually `${BASE_DOMAIN}` or a specific subdomain.
4. **Docker socket proxy permissions** — the proxy must have `CONTAINERS=1` and `NETWORKS=1`.
5. **Check Caddy logs:**
   ```bash
   docker logs <caddy-container-name> 2>&1 | tail -50
   ```
6. **Inspect the live config:**
   ```bash
   # From inside the Caddy container or via its admin API
   curl -s http://localhost:2019/config/ | jq .
   ```

---

### e. HTTPS / TLS issues

- Caddy auto-provisions certs via **Let's Encrypt** using the **Cloudflare DNS-01 challenge**.
- The `CLOUDFLARE_TOKEN` env var must be set on the **Caddy container** (not the service container). The `{env.CLOUDFLARE_TOKEN}` in labels is evaluated in Caddy's context.
- Port 80 is used for HTTP→HTTPS redirects, NOT for ACME HTTP-01 challenge.
- If certs aren't being issued, check:
  - Cloudflare API token has the right permissions (`Zone:DNS:Edit`)
  - DNS records point to the right IP
  - Caddy logs for ACME errors

---

### f. Browser showing wrong page / Google loading

**Possible causes:**

1. **HSTS caching** — the browser remembers a previous redirect. Fix: try **incognito/private window** or clear HSTS cache in `chrome://net-internals/#hsts`.
2. **UFW blocking the port** — service works on `localhost` but not from LAN/external. Caddy gets a connection refused and may serve a fallback.
3. **Caddy's catch-all route** — if the root domain has a `reverse_proxy` (e.g. Dashy), any unmatched path goes there. Check that your route labels are actually being picked up.
4. **DNS caching** — stale DNS pointing to a different server. Use `dig` or `nslookup` to verify.

---

## 5. Label Reference Quick Sheet

### Simple reverse proxy (bridge-network container)

```yaml
labels:
  caddy: "${BASE_DOMAIN}"
  caddy.reverse_proxy: "{{upstreams 8080}}"
```

### Path-based route (prefix kept)

```yaml
labels:
  caddy: "${BASE_DOMAIN}"
  caddy.handle: "/myapp*"
  caddy.handle.0_reverse_proxy: "{{upstreams 8080}}"
```

### Path-based route (prefix stripped)

```yaml
labels:
  caddy: "${BASE_DOMAIN}"
  caddy.handle_path: "/myapp*"
  caddy.handle_path.0_reverse_proxy: "{{upstreams 8080}}"
```

### Subdomain-based route (host-network service)

```yaml
labels:
  caddy: "myapp.${BASE_DOMAIN}"
  caddy.reverse_proxy: "host.docker.internal:8080"
  caddy.tls.dns: "cloudflare {env.CLOUDFLARE_TOKEN}"
  caddy.header.Strict-Transport-Security: "max-age=31536000; includeSubDomains"
```

### Multiple ordered handles (same domain, different backends)

```yaml
labels:
  caddy: "${BASE_DOMAIN}"
  # Specific path → backend A
  caddy.handle_0: "/api*"
  caddy.handle_0.0_reverse_proxy: "host.docker.internal:3000"
  # Catch-all → backend B
  caddy.handle_1: "/*"
  caddy.handle_1.0_reverse_proxy: "host.docker.internal:8080"
  caddy.tls.dns: "cloudflare {env.CLOUDFLARE_TOKEN}"
```

### Path stripping with `uri strip_prefix` inside a handle

```yaml
labels:
  caddy: "${BASE_DOMAIN}"
  caddy.handle_0: "/prefix/sub*"
  caddy.handle_0.0_uri: "strip_prefix /prefix"
  caddy.handle_0.1_reverse_proxy: "host.docker.internal:9090"
```

### Forwarding real client info

```yaml
labels:
  caddy.handle_0.1_reverse_proxy.header_up: "Host {host}"
  caddy.handle_0.1_reverse_proxy.header_up_1: "X-Real-IP {remote_host}"
  caddy.handle_0.1_reverse_proxy.header_up_2: "X-Forwarded-For {remote_host}"
  caddy.handle_0.1_reverse_proxy.header_up_3: "X-Forwarded-Proto {scheme}"
```

### Root domain catch-all (e.g. Dashy dashboard)

```yaml
labels:
  caddy: "${BASE_DOMAIN}"
  caddy.tls.dns: "cloudflare {env.CLOUDFLARE_TOKEN}"
  caddy.reverse_proxy: "{{upstreams 8082}}"
  caddy.header.Strict-Transport-Security: "max-age=31536000; includeSubDomains"
```

---

## 6. Potential Future Upgrades

### a. Wildcard SSL with Cloudflare

Set up `*.home.ashorkqueen.xyz` wildcard cert for all subdomains. This would allow `vpn.home.ashorkqueen.xyz` without needing individual cert provisioning per subdomain:

```yaml
labels:
  caddy: "*.home.ashorkqueen.xyz"
  caddy.tls.dns: "cloudflare {env.CLOUDFLARE_TOKEN}"
```

### b. Subdomain-based routing instead of path-based

Cleaner URLs — `vpn.ashorkqueen.xyz` instead of `home.ashorkqueen.xyz/vpn/`:
- Avoids all the `handle_path` vs `handle` complexity
- Each service gets its own subdomain and site block
- Requires a DNS A or CNAME record per subdomain (or a wildcard record)
- Already in use for Plex (`plex.${BASE_DOMAIN}`) and Jellyfin (`jellyfin.${BASE_DOMAIN}`)

### c. Caddy security headers

Add security headers globally:
```yaml
caddy.header.X-Content-Type-Options: "nosniff"
caddy.header.X-Frame-Options: "SAMEORIGIN"
caddy.header.Referrer-Policy: "strict-origin-when-cross-origin"
caddy.header.Content-Security-Policy: "default-src 'self'"
```
HSTS is already applied per-service; could be made global.

### d. Rate limiting

Caddy has rate limiting via the `rate_limit` plugin. Could protect exposed services from brute-force attacks on login pages.

### e. Access control / IP allowlisting

Restrict admin panels to specific IP ranges:
```
# Conceptual Caddyfile equivalent
@allowed remote_ip 192.168.1.0/24 100.64.0.0/10
handle /admin* {
    @denied not remote_ip 192.168.1.0/24 100.64.0.0/10
    respond @denied 403
    reverse_proxy backend:8080
}
```
Useful for panels like 3x-UI that shouldn't be publicly accessible.

### f. Caddy monitoring / metrics

- Enable Caddy's built-in **Prometheus metrics** endpoint (`/metrics` on the admin API)
- Feed into a Grafana dashboard for traffic visibility
- The admin API already runs on `:2019` (used by the healthcheck)

### g. Automatic certificate management improvements

- **DNS-01 challenge** is already in use — works great for internal-only services that don't have port 80/443 exposed
- Add certificate expiry monitoring/alerting (e.g. via Prometheus `caddy_tls_*` metrics)

### h. Failover / health checking

Caddy can health-check backends and failover between multiple instances:
```
reverse_proxy backend1:8080 backend2:8080 {
    health_uri /health
    health_interval 10s
}
```
Useful if you scale a service to multiple replicas.

---

## Appendix: File Locations

| File | Purpose |
|------|---------|
| `homelab/compose/docker-compose.network.yml` | Caddy container, socket proxy, Dashy, Cloudflared, Tailscale |
| `homelab/compose/docker-compose.media.yml` | Sonarr, Radarr, Prowlarr, Plex, Jellyfin, Overseerr (with Caddy labels) |
| `homelab/compose/docker-compose.llm.yml` | LLM services, internal dashboard (with Caddy labels) |
| `3x-ui/docker-compose.yml` | 3x-UI panel — standalone, host-network, labels for `/vpn` routing |
| `homelab/data/caddy/Caddyfile` | Static fallback config for LAN-only `:80` routes |
| `homelab/data/caddy/data/` | Caddy persistent data (certs, ACME state) |
| `homelab/data/caddy/config/` | Caddy auto-generated config |

---

*Last updated: May 2026*
