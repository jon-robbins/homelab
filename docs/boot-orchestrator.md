# Homelab boot orchestrator

Ordered post-reboot bring-up for media mounts, Docker bind mounts, Tailscale port binds, external-tunnel/VPN, and compose worker settle time.

## Why

Docker can start containers before `/mnt/media-hdd` / `/mnt/media-nvme` are the real disks (fstab uses `nofail`). Bind mounts then attach empty root-FS stubs and stay wrong until `--force-recreate`. Tailscale `:443` has the same class of race (address not yet on `tailscale0`).

## Components

| Piece | Role |
|-------|------|
| [`scripts/ops/homelab-boot-orchestrator.sh`](../scripts/ops/homelab-boot-orchestrator.sh) | Phases P0–P6 |
| [`scripts/ops/verify-media-mounts.sh`](../scripts/ops/verify-media-mounts.sh) | Host + in-container mount sanity |
| [`scripts/ops/start-tailscale-bound-services.sh`](../scripts/ops/start-tailscale-bound-services.sh) | Back-compat wrapper → P3–P4 only |
| [`config/systemd/homelab-boot-orchestrator.service`](../config/systemd/homelab-boot-orchestrator.service) | Systemd oneshot after Docker/mounts and `homelab-infisical-agent.service` |
| `data/boot-orchestrator/status.json` | Last run result (runtime host) |

## Install (runtime host `${HOMELAB_ROOT}`)

```bash
sudo cp "${HOMELAB_ROOT}/config/systemd/homelab-boot-orchestrator.service" \
  /etc/systemd/system/homelab-boot-orchestrator.service
sudo systemctl daemon-reload
sudo systemctl enable --now homelab-boot-orchestrator.service
# Prefer the full orchestrator; disable the old Tailscale-only unit to avoid double recreate:
sudo systemctl disable --now homelab-tailscale-binds.service
# Confirm only one boot helper is enabled:
systemctl is-enabled homelab-boot-orchestrator.service
systemctl is-enabled homelab-tailscale-binds.service   # expect disabled/masked
```

`ExecStart` and `WorkingDirectory` must stay on the **runtime host** (`${HOMELAB_ROOT}`), never a git worktree path.

`homelab-boot-orchestrator.service` specifies `After=homelab-infisical-agent.service` and `Wants=homelab-infisical-agent.service` to ensure the Infisical Agent starts before Compose services are brought up.

Manual run:

```bash
cd "${HOMELAB_ROOT}"
./scripts/ops/homelab-boot-orchestrator.sh
./scripts/ops/homelab-boot-orchestrator.sh --dry-run          # skip recreate/VPN up; waits still apply and may fail
./scripts/ops/homelab-boot-orchestrator.sh --phases P0,P1
./scripts/ops/homelab-boot-orchestrator.sh --status
./scripts/ops/verify-media-mounts.sh --json
```

`--dry-run` does **not** mean “always exit 0”: mount/Tailscale/VPN wait gates still run. It only skips force-recreate and VPN `compose up`. It rewrites `status.json`.

## Phases

| Phase | Gate | Action | Failure reason |
|-------|------|--------|----------------|
| P0 | Media mounts size floors + not OS root | wait | `mounts_not_ready` |
| P1 | `docker info` | wait | `docker_not_ready` |
| Pre-P2 | Rendered secret files exist & non-empty (`${HOME}/.config/homelab/secrets`) | gate check | `secrets_not_ready` |
| P2 | mounts OK | force-recreate mount-bind services | `mount_bind_recreate_failed` / `stale_media_bind` |
| P3 | `${TAILSCALE_IP}` on `tailscale0` | wait | `tailscale_ip_missing` |
| P4 | TS ready | force-recreate `caddy`, `homelab-mcp` | `tailscale_bind_recreate_failed` |
| P5 | external-tunnel panel `/vpn` | `docker compose up -d` in `config/external-tunnel` | `vpn_panel_not_ready` (soft: `vpn_xray_sync_drift`) |
| P6 | settle sleep | probe critical health + configured ingress routes | `dependency_not_ready` / `ingress_route_not_ready` |

Default settle: `HOMELAB_BOOT_SETTLE_SEC=120`. Mount wait: `HOMELAB_BOOT_MOUNT_WAIT_SEC=120`.

### Infisical Offline Boot Behavior
Because the Infisical Agent writes persistent files under `${HOME}/.config/homelab/secrets/`, a reboot during an Infisical Cloud outage still succeeds as long as secret files were previously rendered. The orchestrator checks that required secret files are present and non-empty; if Infisical is offline, previously rendered files remain valid on disk.

## Reason → remediation catalog

| Reason | Meaning | Primary fix |
|--------|---------|-------------|
| `mounts_not_ready` | HDD/NVMe not mounted or still the OS stub | `findmnt /mnt/media-hdd`; check disk/fstab UUID; remount; re-run orchestrator |
| `stale_media_bind` | Container `df` shows OS root on a media path | `docker compose up -d --no-deps --force-recreate <svc>` after mounts are real (P2) |
| `docker_not_ready` | Docker daemon not answering | `systemctl status docker`; `journalctl -u docker` |
| `secrets_not_ready` | Required rendered secret files missing/empty in `${HOME}/.config/homelab/secrets` | Check `systemctl status homelab-infisical-agent.service` and `journalctl -u homelab-infisical-agent.service`; ensure initial Agent render completed |
| `mount_bind_recreate_failed` | Compose recreate error in P2 | Inspect `docker compose logs <svc>`; fix mounts/permissions |
| `tailscale_ip_missing` | `tailscale0` lacks `${TAILSCALE_IP}` | Fix Tailscale container / `.env`; wait; re-run P3–P4 |
| `tailscale_bind_recreate_failed` | Caddy/MCP recreate failed | Recreate after IP is present; check port conflicts with external-tunnel `:443` |
| `vpn_panel_not_ready` | external-tunnel `/vpn` not answering | `cd config/external-tunnel && docker compose up -d`; check `:26435` |
| `vpn_xray_sync_drift` | Panel DB vs Xray client mismatch (boot continues) | Soft `warnings[]` entry; run `scripts/tunnel/check-3xui-health.sh`; fix panel clients |
| `dependency_not_ready` | Critical service still unhealthy after settle | Inspect that service’s logs/`status.json`; fix root cause — do not blind-restart everything |
| `ingress_route_not_ready` | A configured Caddy/Tailscale user route did not answer with an accepted HTTP status after settle | Check Tailscale IP, Caddy bind/route, qBittorrent WebUI, and `boot.ingress_smoke` in workers YAML |
| `last_run_failed` | Worker status `failed>0` (e.g. offload) after settle | Soft `warnings[]` entry; check `data/qbt-offload/status.json` and worker logs |

Successful boots may still set `reason` to the first soft warning while `ok: true` and `warnings: [...]` list all soft codes. Systemd retries are capped (`StartLimitBurst=3`); recreate phases are skipped if already completed for the current `boot_id`.

## Environment knobs

| Variable | Default | Purpose |
|----------|---------|---------|
| `HOMELAB_BOOT_MOUNT_WAIT_SEC` | 120 | P0 timeout |
| `HOMELAB_BOOT_DOCKER_WAIT_SEC` | 60 | P1 timeout |
| `TAILSCALE_BIND_WAIT_SEC` | 180 | P3 timeout |
| `HOMELAB_BOOT_VPN_WAIT_SEC` | 120 | P5 timeout |
| `HOMELAB_BOOT_SETTLE_SEC` | 120 | P6 sleep before health probe |
| `HOMELAB_BOOT_MOUNT_BIND_SERVICES` | (see script) | Comma-separated compose services for P2 |
| `HOMELAB_BOOT_TS_BIND_SERVICES` | `caddy,homelab-mcp` | P4 services |
| `HOMELAB_BOOT_CRITICAL_HEALTH_SERVICES` | `caddy,qbittorrent,cloudflared,homelab-qbt-offload` | P6 hard-fail list |
| `MEDIA_HDD_MIN_KIB` / `MEDIA_NVME_MIN_KIB` | 2 TiB / 1 TiB | Stub-mount size floors |

Route-level boot smoke checks live in `boot.ingress_smoke` in the mounted workers YAML file (`config/workers.yaml.example` by default, or `WORKERS_CONFIG_PATH` when set). The default route probes `http://${TAILSCALE_IP}/qbittorrent/` with `Host: example-homelab` and accepts `200`, `401`, or `403`.

## Non-goals (v1)

- Continuous mid-uptime remount watchdog
- Merging external-tunnel into root compose
- Removing fstab `nofail`
- Full-stack `docker compose up -d` every boot

## Optional Docker ordering (advisory)

A systemd drop-in for `docker.service` with `After=mnt-media\x2dhdd.mount mnt-media\x2dnvme.mount` can reduce races but **does not** guarantee correct binds if the disk appears late after Docker has already created containers. P2 force-recreate remains the hard guarantee.

```bash
# Example only — not installed by default
sudo mkdir -p /etc/systemd/system/docker.service.d
printf '%s\n' '[Unit]' 'After=mnt-media\x2dhdd.mount mnt-media\x2dnvme.mount' \
  | sudo tee /etc/systemd/system/docker.service.d/after-media-mounts.conf
sudo systemctl daemon-reload
```

## Reboot checklist

1. `systemctl is-active homelab-boot-orchestrator.service`
2. `./scripts/ops/homelab-boot-orchestrator.sh --status` → `ok: true`, `phase: P6`
3. `./scripts/ops/verify-media-mounts.sh` and spot-check `docker exec … df` on offload/jellyfin
4. Tailscale HTTPS: Caddy listening on `${TAILSCALE_IP}:443`
5. VPN panel: `curl -sf http://127.0.0.1:26435/vpn`
