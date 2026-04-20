# Cloudflare Tunnel (`cloudflared`) — Docker

All **cloudflared** Tunnel **config, credentials, and docs** for this host live here: **`/home/jon/docker/data/cloudflared`**.

If you use the `cloudflared` CLI on the host, keep its origin cert and tunnel credentials alongside this directory (many setups symlink `~/.cloudflared` → this folder so `cloudflared tunnel login` writes `cert.pem` here).

Official references (check current docs if something changes):

- [Install `cloudflared` (CLI)](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/)
- [Create a tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/tunnel-guide/)
- [Docker — `cloudflare/cloudflared` image](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/tunnel-guide/#run-as-a-service-on-docker)

## Layout

| Path | Purpose |
|------|--------|
| `cert.pem` | Origin certificate from `cloudflared tunnel login` (**never commit**) |
| `config.yml` | Tunnel ingress — copy from `config.yml.example` — **never commit** |
| `config.yml.example` | Example ingress config (safe to commit) |
| `credentials/` | Tunnel `*.json` credentials (**never commit**) |

## One-time: create tunnel and credentials (CLI on the server)

```bash
cloudflared tunnel login
cloudflared tunnel create qbittorrent
```

Note the **tunnel UUID**. Credential files live under **`/home/jon/docker/cloudflared/credentials/`** (and `ls ~/.cloudflared/` shows the same, via symlink).

Create `config.yml` from the example and set:

- `tunnel:` → your UUID  
- `credentials-file:` → `/etc/cloudflared/credentials/<TUNNEL_UUID>.json` (path **inside** the container; compose mounts `./credentials` there)  
- `ingress` → only hostnames you intend to expose. Keep other admin UIs off the tunnel (LAN/VPN).

Point DNS at the tunnel:

```bash
cloudflared tunnel route dns homelab seerr.example.com
```

## Run the tunnel (Docker)

The `cloudflare/cloudflared` image runs as **UID 65532**. On a bind mount, that UID must be able to **traverse** the directory and read the JSON — use `chmod 755` on `credentials/` and `chmod 644` on the `*.json` (see `hardening/secure-secret-file-permissions.sh`). A `700` directory breaks cloudflared after container restarts (“credentials file doesn't exist”). Do not store unrelated secrets in that directory.

**Do not** mount `credentials/` read-only (`:ro`). `cloudflared` opens the tunnel JSON in a way that returns “permission denied” on a read-only bind mount even when the file mode would allow reading.

You may see log lines about `/home/jon/.cloudflared/<uuid>.json` during startup; that is a default lookup before `credentials-file` in `config.yml` is used.

```bash
cd /home/jon/docker
docker compose -f docker-compose.network.yml up -d cloudflared
docker compose -f docker-compose.network.yml logs -f cloudflared
```

## If `cloudflared` is installed from apt

Stop the system service so only the container runs:

```bash
sudo systemctl disable --now cloudflared 2>/dev/null || true
```

Optional: `sudo apt remove -y cloudflared` and use a standalone `cloudflared` binary for CLI only.

## Troubleshooting

- **502 / connection refused:** `curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080` on the host (e.g. `401` before login is OK).
- **Web UI broken through Cloudflare:** disable **Rocket Loader** for this hostname. See [qBittorrent #21673](https://github.com/qbittorrent/qBittorrent/issues/21673).
