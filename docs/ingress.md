# Ingress Architecture

Canonical reference for WAN/LAN/Tailscale port ownership and operator verification. VPN management is outside this project. For per-service URLs and internal Docker access, see [`network-access.md`](./network-access.md). For Caddyfile route blocks, see [`caddy-guide.md`](./caddy-guide.md).

---

## Summary

| Port | Listener | Consumer | Reachability |
|------|----------|----------|--------------|
| **443** | Caddy (bound to example tailnet IP) | Homelab HTTPS (`{$BASE_DOMAIN}`, subdomains) | **Example tailnet** — `192.0.2.10:443` → Caddy |
| **8443** | Caddy | Media subdomains (`stream.*`, `jf.*`, `share.*` → FileBrowser Quantum) + full `home.*` site on WAN | **WAN** — grey-cloud DNS + router forward; also LAN |
| **80** | Caddy | Arr stack subpaths, LAN HTTP mirror | **LAN / Tailscale** — no WAN TLS redirect to `:8443` on public IP |
| **10443** | — | *(unused in production)* | Historical Path A only |
| Tunnel | `cloudflared` | `home.*`, `get.*` HTTPS | **WAN** — Cloudflare edge; no open inbound ports for those names |

---

## Production path (2026-06-19+)

Path A (nginx SNI router + Xray fallbacks multiplexing on `:443`) was **rejected**. Production uses **Plan B**:

```text
192.0.2.10:443   → reserved example listener
192.0.2.10:8443  → Caddy → Plex / Jellyfin / FileBrowser Quantum (grey-cloud DNS)

stream.example.homelab.local:8443  → Plex
jf.example.homelab.local:8443      → Jellyfin
share.example.homelab.local:8443   → FileBrowser Quantum

home.* / get.*  → Cloudflare Tunnel (unchanged)
```

- VPN and host-level tunnel management are outside this project.
- **Media** uses grey-cloud A records and WAN `:8443` → host `:8443` → Caddy (`stream.*` / `jf.*` / `share.*` blocks in `config/caddy/Caddyfile`). FileBrowser Quantum serves `share.*` (read-only NVMe/HDD browse + opaque share links). `scripts/ops/media-dns.sh` manages grey-cloud A records for the example media hosts together — do **not** add `share.*` to Cloudflare Tunnel.
- Opening WAN `:8443` also exposes `https://home.*:8443` (full Caddy site). Sensitive `home.*` paths must pass `scripts/ops/test-caddy-wan-paths.sh` before sign-off.


### Apply media ingress (operator runbook excerpt)

```bash
# State gate
sudo ss -ltnp | grep -E ':443|:8443'

# 1. Caddy — deploy stream/jf blocks, then:
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile

# 2. Firewall — allow WAN 8443
sudo nft -f ./scripts/hardening/nftables-arr-stack.nft

# 3. Router — WAN 8443/tcp → host :8443

# 4. DNS — grey-cloud A records
./scripts/ops/media-dns.sh preflight
./scripts/ops/media-dns.sh apply --yes
```

Full cutover steps, app URLs, and external verification are covered by this ingress guide.

---

## Tailscale parallel path

Caddy publishes **`${TAILSCALE_IP}:443:443`** (Tailscale IP only) in `compose/network.yml`. Tailscale clients reach the full homelab HTTPS site on `:443` without touching `:8443`.

**Boot race:** Docker may start before media mounts or `tailscale0` are ready. Binding `${TAILSCALE_IP}:443` then fails (`cannot assign requested address`), and media bind mounts can attach empty root-FS stubs until recreate. **`homelab-boot-orchestrator.service`** runs [`scripts/ops/homelab-boot-orchestrator.sh`](../scripts/ops/homelab-boot-orchestrator.sh) after Docker/mounts (phases P0–P6: mounts → recreate media binds → Tailscale → recreate `caddy`/`homelab-mcp` → external-tunnel → settle). See [`docs/boot-orchestrator.md`](boot-orchestrator.md). Legacy `homelab-tailscale-binds.service` only runs P3–P4 and should be disabled once the full orchestrator is enabled.

LAN users can also use Caddy `:80` (HTTP) or Tailscale `:443` (HTTPS).

---

## Cloudflare Tunnel

`home.*` and `get.*` continue to ingress through **Cloudflare Tunnel** — no public A records to the homelab IP for those names. Tunnel config, credentials, and compose layout: [`config/cloudflared/README.md`](../config/cloudflared/README.md).

After media works on `:8443`, remove `stream.*` / `jf.*` / `share.*` from `data/cloudflared/config.yml` so media and file sharing are not double-pathed through the tunnel. Keep `share.*` off the tunnel permanently (same grey-cloud path as `stream` / `jf`).

---

---

## Deprecated approaches

**nginx SNI router** and **Xray TLS fallbacks** on `:443` were tested and abandoned (2026-06-19). nginx in front of Xray broke Vision; fallback SNIs could not present valid certs for media hostnames. The experimental `nginx-sni` compose service and Path A cutover scripts were removed in the pre-app cleanup.

**Erugo** on `share.*` was replaced by **FileBrowser Quantum** (`compose/files.yml` → `filebrowser:80`). Repo no longer ships Erugo compose/config; runtime orphan container / NVMe leftovers are Wave 3 cleanup only.

Historical detail:

- Path A canary POC — removed; see [Deprecated approaches](#deprecated-approaches) above

---

## Operator checks

```bash
# Who owns 443 / 8443?
sudo ss -ltnp | grep -E ':443|:8443'

# WAN exposure gates for sensitive home.* paths on :8443
./scripts/ops/test-caddy-wan-paths.sh
```

---

*Last updated: July 2026*
