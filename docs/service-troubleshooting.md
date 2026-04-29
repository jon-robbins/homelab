# Service Troubleshooting Guide

Reference for common issues when modifying Docker Compose services in this homelab stack. Each entry lists the **symptom**, **root cause**, and **fix**.

---

## 1. Container Healthcheck Constraints

### 1.1 cloudflared — no shell, no HTTP clients

- **Symptom:** Any `CMD-SHELL` healthcheck or `curl`/`wget` probe fails immediately.
- **Cause:** The `cloudflare/cloudflared` image is extremely stripped — no `/bin/sh`, no `curl`, no `wget`, no `cat`.
- **Fix:** Use `CMD` form (not `CMD-SHELL`) with the native binary:
  ```yaml
  healthcheck:
    test: ["CMD", "cloudflared", "tunnel", "--metrics", "0.0.0.0:20241", "ready"]
  ```
- **Important:** Pin the metrics port with `--metrics 0.0.0.0:20241` in the `tunnel run` command. Without it, cloudflared picks a random port (20241–20245 range) and the `ready` probe can't find it.
- **Wrong:** `["CMD", "cloudflared", "tunnel", "info", "--output", "json"]` — `tunnel info` requires a tunnel name/ID argument.

### 1.2 Ollama — no curl AND no wget

- **Symptom:** `curl -sf http://localhost:11434/` healthcheck always fails.
- **Cause:** The `ollama/ollama` image (Ubuntu 24.04 minimal) has neither `curl` nor `wget`.
- **Fix:** Use bash `/dev/tcp`:
  ```yaml
  healthcheck:
    test: ["CMD-SHELL", "bash -c 'echo > /dev/tcp/localhost/11434' || exit 1"]
  ```

### 1.3 Alpine-based images (Dashy) — IPv6 localhost resolution

- **Symptom:** `wget -q --spider http://localhost:PORT/` returns "Connection refused" even though the service is running.
- **Cause:** Alpine's `wget` resolves `localhost` to `::1` (IPv6), but Node.js services often bind only to `0.0.0.0` (IPv4).
- **Fix:** Use `127.0.0.1` explicitly:
  ```yaml
  healthcheck:
    test: ["CMD-SHELL", "wget -q --spider http://127.0.0.1:8082/ || exit 1"]
  ```

### 1.4 qBittorrent — authenticated API endpoints

- **Symptom:** Healthcheck on `/api/v2/app/version` returns HTTP 403.
- **Cause:** qBittorrent API requires authentication; unauthenticated requests get 403.
- **Fix:** Use the root path which serves the login page without auth:
  ```yaml
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8080/ || exit 1"]
  ```

---

## 2. Security Hardening Pitfalls

### 2.1 Linuxserver.io containers — s6-overlay needs capabilities

- **Symptom:**
  ```
  chown: changing ownership of '/app': Operation not permitted
  s6-applyuidgid: fatal: unable to set supplementary group list: Operation not permitted
  ```
- **Cause:** `security_opt: ["no-new-privileges:true"]` blocks s6-overlay's setuid/setgid init system, even when capabilities are present. `cap_drop: [ALL]` alone removes required caps.
- **Fix:** Remove `no-new-privileges:true` from Linuxserver.io containers (sonarr, radarr, readarr, prowlarr, qbittorrent) and add:
  ```yaml
  cap_drop: [ALL]
  cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE, FOWNER]
  ```
- **Note:** Keep `cap_drop: [ALL]` for defense-in-depth — the explicit `cap_add` re-grants only what s6 needs.

### 2.2 Caddy — TLS certificate permissions under cap_drop

- **Symptom:** Caddy fails with "permission denied" loading TLS certificates.
- **Cause:** Without `DAC_OVERRIDE`, Caddy (even as root) can't access cert files owned by host users.
- **Fix:** Ensure `/data/caddy/certificates/` and subdirectories are owned by `root:root`, or add `DAC_OVERRIDE` to `cap_add`.

---

## 3. Caddy Reverse Proxy Routing

All routing is defined in `caddy/Caddyfile` — there are no Docker labels involved.

### 3.1 Subpath routing — handle vs handle_path

| Caddyfile Directive | Strips prefix? | When to use |
|---------------------|---------------|-------------|
| `handle /app* { reverse_proxy app:port }` | No | App natively supports a URL base (e.g., Arr apps with `UrlBase`) |
| `handle_path /app* { reverse_proxy app:port }` | Yes | App does NOT support a URL base (e.g., qBittorrent) |

When using `handle_path`, always pair with a trailing-slash redirect:
```caddyfile
redir /app /app/ 301
handle_path /app/* {
    reverse_proxy app:8080
}
```

### 3.2 Prowlarr UrlBase alignment

- **Symptom:** Prowlarr UI loads blank page, JS assets return 404.
- **Cause:** `handle_path` in the Caddyfile strips the prefix, but Prowlarr generates absolute URLs based on `UrlBase`. If `UrlBase` is empty, assets reference `/index.js` which doesn't route to Prowlarr.
- **Fix:** Set `UrlBase` in Prowlarr's `config.xml`:
  ```xml
  <UrlBase>/prowlarr</UrlBase>
  ```
  Then use `handle` (not `handle_path`) in `caddy/Caddyfile`:
  ```caddyfile
  handle /prowlarr* {
      reverse_proxy prowlarr:9696
  }
  ```

---

## 4. Profile-Gated Services

### 4.1 Gluetun VPN — requires configuration to start

- **Symptom:** `ERROR default route not found: in N route(s)` and immediate exit.
- **Cause:** Gluetun needs VPN provider credentials (`VPN_SERVICE_PROVIDER`, `WIREGUARD_PRIVATE_KEY`, etc.) in `.env`. Without them, it crashes immediately.
- **Fix:** Keep gluetun behind `profiles: ["vpn"]` until VPN is configured. qBittorrent uses `networks: [homelab_net]` directly (no VPN sidecar dependency).

---

## 5. Quick Reference — Healthcheck Commands

| Service | Image Base | Working Healthcheck |
|---------|-----------|---------------------|
| cloudflared | `cloudflare/cloudflared` (no shell) | `["CMD", "cloudflared", "tunnel", "--metrics", "0.0.0.0:20241", "ready"]` |
| ollama | `ollama/ollama` (no curl/wget) | `["CMD-SHELL", "bash -c 'echo > /dev/tcp/localhost/11434' || exit 1"]` |
| dashy | `lissy93/dashy` (Alpine) | `["CMD-SHELL", "wget -q --spider http://127.0.0.1:8082/ || exit 1"]` |
| qbittorrent | `linuxserver/qbittorrent` | `["CMD-SHELL", "curl -sf http://localhost:8080/ || exit 1"]` |
| arr services | `linuxserver/*` | `["CMD-SHELL", "curl -sf http://localhost:PORT/ping || exit 1"]` |
| prowlarr | `linuxserver/prowlarr` | `["CMD-SHELL", "curl -sf http://localhost:9696/prowlarr/ping || exit 1"]` |
| caddy | `local/caddy-cf:latest` | `["CMD", "wget", "-q", "--spider", "http://127.0.0.1:2019/config/"]` |
| internal-dashboard | custom (Node) | `["CMD-SHELL", "curl -sf http://127.0.0.1:8080/ || exit 1"]` |
| openclaw-gateway | custom (Node) | `["CMD-SHELL", "node -e \"fetch('http://127.0.0.1:18789/healthz')...\""]` |
