# Scripts layout

Host-side helpers grouped by function. Long-running services live in `compose/docker-compose.*.yml` (included by the root `docker-compose.yml`); the canonical worker package lives at [`src/homelab_workers/`](../src/homelab_workers/).

```
scripts/
├── ci/          CI orchestration (local runs, watchers, `act` wrappers)
├── ops/         Host bootstrap, deploy, backup, one-off remediation
├── media/       Arr-stack & media-server helpers
├── vpn/         3x-ui / Xray Reality deployment helpers & diagnostics
└── hardening/   Security lockdown (file perms, nftables)
```

## ci/

| Path | Purpose |
|------|---------|
| [ci/ci-local.sh](ci/ci-local.sh) | Local mirror of `.github/workflows/ci.yml` + nightly E2E |
| [ci/ci-watch.sh](ci/ci-watch.sh) | Poll the latest CI run for the current branch and print failing logs |
| [ci/gh-actions-local.sh](ci/gh-actions-local.sh) | Run the GitHub Actions workflow locally via [`act`](https://nektosact.com/) |

## ops/

| Path | Purpose |
|------|---------|
| [ops/setup.sh](ops/setup.sh) | First-run bootstrap: `.env` creation/update prompts, config templates, GPU detection note, compose validation. Pass `--harden` to chain hardening scripts. |
| [ops/backup-data.sh](ops/backup-data.sh) | Pre-deploy archive of `./data/` under `${MEDIA_HDD_PATH}/backups/homelab-data/`. Invoked by nightly deploy. |
| [ops/fix-media-permissions.sh](ops/fix-media-permissions.sh) | One-off host helper for media directory ownership/modes |
| [ops/dns-split-horizon.sh](ops/dns-split-horizon.sh) | Migrate LAN IP records out of Cloudflare public DNS into Pi-hole local DNS |
| [ops/render-readme-mermaid.sh](ops/render-readme-mermaid.sh) | Render the Mermaid diagrams in `README.md` to `docs/images/*.svg` locally |

## media/

| Path | Purpose |
|------|---------|
| [media/configure-bazarr.sh](media/configure-bazarr.sh) | Post-boot Bazarr configuration via its REST API |
| [media/configure_remote_path_mappings.py](media/configure_remote_path_mappings.py) | Create Sonarr/Radarr Remote Path Mappings via the v3 API |
| [media/monitor-stuck-downloads.py](media/monitor-stuck-downloads.py) | Detect stuck Arr downloads and notify via Telegram |
| [media/reset_indexers.py](media/reset_indexers.py) | Recycle Prowlarr indexers |
| [media/media_action_router.py](media/media_action_router.py) | Strict two-phase media action router (parse → execute → format) used by the media-agent |
| [media/debug_openclaw_media_llm.py](media/debug_openclaw_media_llm.py) | Debug harness: one agent turn, classify reply, append NDJSON to session log |

## vpn/

| Path | Purpose |
|------|---------|
| [vpn/china-diag.sh](vpn/china-diag.sh) | Connectivity diagnostic for the Xray Reality endpoint, run from a client in restrictive networks |
| [vpn/xray-test.sh](vpn/xray-test.sh) | Spin up a local Xray client against the homelab Reality inbound and verify tunnel + DNS + speed |
| [vpn/test-xray-client.sh](vpn/test-xray-client.sh) | Higher-level client test for the Reality VPN (macOS-oriented) |
| [vpn/mac-setup.sh](vpn/mac-setup.sh) | macOS client installer (Homebrew Xray, launchd/system proxy plumbing) |

The 3x-ui compose file and `SETUP.md` live in [`3x-ui/`](../3x-ui/).

## hardening/

| Path | Purpose |
|------|---------|
| [hardening/secure-secret-file-permissions.sh](hardening/secure-secret-file-permissions.sh) | Lock down permissions on secret files |
| [hardening/nftables-arr-stack.nft](hardening/nftables-arr-stack.nft) | Host firewall rules for the arr stack |
| [hardening/SECURITY_AUDIT.md](hardening/SECURITY_AUDIT.md) | Audit findings and remediation notes |

---

Tests live under [`tests/`](../tests/) at the repo root: `compose/`, `runtime/`, `integration/`, `workers/`.

### Worker runtime

Production retries use Sonarr/Radarr **Failed Download Handling** (no dedicated worker container).
