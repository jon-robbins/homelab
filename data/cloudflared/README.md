# Cloudflare Tunnel (`cloudflared`)

Tunnel **config**, **credentials**, and this README live under **`./data/cloudflared/`** (repo root).

Many operators symlink `~/.cloudflared` → `./data/cloudflared` so `cloudflared tunnel login` writes `cert.pem` next to the compose mount.

Official docs:

- [Install cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/)
- [Create a tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/tunnel-guide/)
- [Docker image](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/tunnel-guide/#run-as-a-service-on-docker)

## Layout

| Path (under `./data/cloudflared/`) | Purpose |
|--------------------------------------|---------|
| `cert.pem` | Origin cert from `cloudflared tunnel login` (**never commit**) |
| `config.yml` | Ingress — copy from `config.yml.example` (**never commit**) |
| `config.yml.example` | Safe template (**commit**) |
| `credentials/` | Tunnel `*.json` (**never commit**) |

## One-time CLI setup (on the server)

```bash
cloudflared tunnel login
cloudflared tunnel create homelab
```

Note the tunnel UUID. Credential JSON files go in **`./data/cloudflared/credentials/`** (same tree as compose bind mounts).

Create `config.yml` from the example:

- `tunnel:` → your UUID
- `credentials-file:` → `/etc/cloudflared/credentials/<UUID>.json` (path **inside** the container; compose mounts `./data/cloudflared/credentials` there)
- `ingress` → only hostnames you intend to expose; keep admin UIs off the public internet

DNS routes (example):

```bash
cloudflared tunnel route dns homelab seerr.example.com
```

## Run with Docker

From the **repo root**:

```bash
docker compose up -d cloudflared
docker compose logs -f cloudflared
```

The `cloudflare/cloudflared` image runs as **UID 65532**. On bind mounts it must **traverse** `credentials/` — use `chmod 755` on the directory and `644` on `*.json` (see [`scripts/hardening/secure-secret-file-permissions.sh`](../../scripts/hardening/secure-secret-file-permissions.sh)). A `700` directory often breaks cloudflared after restart.

Do **not** mount `credentials/` read-only (`:ro`); cloudflared may fail to open the file.

## If cloudflared is installed from apt

Avoid double-running:

```bash
sudo systemctl disable --now cloudflared 2>/dev/null || true
```

## Troubleshooting

- **502 / connection refused**: on the host, `curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:<service-port>` (e.g. `401` before login is OK).
- **Broken UI through Cloudflare**: disable **Rocket Loader** for that hostname.
