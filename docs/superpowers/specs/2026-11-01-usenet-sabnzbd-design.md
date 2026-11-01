# Usenet (Eweka + SABnzbd) Technical Design Spec

- **Author:** Antigravity (Engineering Lead)
- **Date:** 2026-11-01
- **Status:** Approved
- **Governed by:** [0001](../../decisions/0001-compose-modules.md), [0002](../../decisions/0002-public-platform-private-site.md), [0003](../../decisions/0003-tiers.md), [0008](../../decisions/0008-compose-policy.md), [0009](../../decisions/0009-image-trust.md), [0010](../../decisions/0010-secrets.md), [0015](../../decisions/0015-exposure-policy.md), [0017](../../decisions/0017-personal-services-are-plugins.md)

---

## 1. Context & Motivation

The homelab runs media automation services (Sonarr, Radarr, Lidarr, Prowlarr) with qBittorrent and Soulseek as existing download sources. Usenet provides retention-backed, full-line-speed downloads, particularly valuable for deep backfills and lossless media.

The owner has registered an account with **Eweka** (`news.eweka.nl`, SSL port `563`, 50 connections) and added the credentials to Infisical under project `homelab`, environment `Production`, folder `/media` (`EWEKA_USERNAME`, `EWEKA_PASSWORD`).

This document specifies the integration of Usenet via **SABnzbd** as a first-class catalog module in the `app` tier, following repository conventions and invariants: zero exposed WAN ports, secret separation via Infisical tmpfs rendering, and zero manual web UI configuration via runbook automation.

---

## 2. Architecture & Service Module

### 2.1 Compose Specification (`modules/app/sabnzbd.compose.yml`)

```yaml
services:
  sabnzbd:
    image: lscr.io/linuxserver/sabnzbd:5.1.3-ls279@sha256:a7cfba459929f73b83c98b476a845076dc72524af80b24fcb4200684e6b1b675
    pids_limit: 500
    networks: [homelab_net]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE, FOWNER]
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - TZ=${TZ}
      - UMASK=002
    volumes:
      - type: bind
        source: ${APP_DATA_DIR}/sabnzbd
        target: /config
        bind: {create_host_path: false}
      - type: bind
        source: ${DOWNLOADS_DIR}
        target: /downloads
        bind: {create_host_path: false}
    labels:
      homelab.state: stateful
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:8080/api?mode=version >/dev/null || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    restart: unless-stopped

networks:
  homelab_net:
    external: true
```

### 2.2 Invariant & Policy Verification
- **[0001] One service per Compose file**: Isolated in `modules/app/sabnzbd.compose.yml`.
- **[0003] Tier boundaries**: Belongs to `app` tier, managed by `homelabctl`.
- **[0008] Compose policy**:
  - Image is pinned with full sha256 digest (`lscr.io/linuxserver/` is on the allowlist).
  - Explicit capabilities dropped (`ALL`) and minimal allowed set added (`CHOWN`, `SETUID`, `SETGID`, `DAC_OVERRIDE`, `FOWNER`).
  - `pids_limit` set to 500.
  - Binds are strictly under `${APP_DATA_DIR}/sabnzbd` and `${DOWNLOADS_DIR}` (declared bind roots).
  - Stateful label `homelab.state: stateful` ensures `homelab-backup` snapshots `${APP_DATA_DIR}/sabnzbd`.
- **[0015] Exposure policy**: No ports are published to host interfaces. Admin UI is reachable internally via `homelab_net` and externally only through Caddy on LAN/Tailscale.

---

## 3. Secret Management & Infisical Templates

### 3.1 Infisical Templates
Templates are stored in `config/infisical/templates/` and copied to `/srv/homelab/infisical/templates/` on the host:
- `config/infisical/templates/EWEKA_USERNAME.tmpl`:
  ```gotemplate
  {{- with listSecretsByProjectSlug "homelab-r-tg-j" "prod" "/media" -}}
  {{- range . -}}
  {{- if eq .Key "EWEKA_USERNAME" -}}{{ .Value }}{{ end -}}
  {{- end -}}
  {{- end -}}
  ```
- `config/infisical/templates/EWEKA_PASSWORD.tmpl`:
  ```gotemplate
  {{- with listSecretsByProjectSlug "homelab-r-tg-j" "prod" "/media" -}}
  {{- range . -}}
  {{- if eq .Key "EWEKA_PASSWORD" -}}{{ .Value }}{{ end -}}
  {{- end -}}
  {{- end -}}
  ```
- `config/infisical/templates/SABNZBD_API_KEY.tmpl`:
  ```gotemplate
  {{- with listSecretsByProjectSlug "homelab-r-tg-j" "prod" "/media" -}}
  {{- range . -}}
  {{- if eq .Key "SABNZBD_API_KEY" -}}{{ .Value }}{{ end -}}
  {{- end -}}
  {{- end -}}
  ```
- `config/infisical/templates/NZBGEEK_API_KEY.tmpl`:
  ```gotemplate
  {{- with listSecretsByProjectSlug "homelab-r-tg-j" "prod" "/mcp" -}}
  {{- range . -}}
  {{- if eq .Key "NZBGEEK_API_KEY" -}}{{ .Value }}{{ end -}}
  {{- end -}}
  {{- end -}}
  ```

### 3.2 Infisical Agent Configuration
Update `config/infisical/agent-config.yaml.example` to include the destination paths in `/run/homelab-secrets/`:
- `/run/homelab-secrets/EWEKA_USERNAME`
- `/run/homelab-secrets/EWEKA_PASSWORD`
- `/run/homelab-secrets/SABNZBD_API_KEY`
- `/run/homelab-secrets/NZBGEEK_API_KEY`

Secrets remain strictly in tmpfs (`/run/homelab-secrets`), with `0640` permissions, and are never committed to git, releases, or environment blocks.

---

## 4. Ingress & Routing (`homelab-site`)

### 4.1 Caddy Routing (`homelab-site/edge/Caddyfile`)
1. **Protected Route:** Under the authenticated route block behind Authentik:
   ```caddyfile
   handle /sabnzbd* {
       import admins_only
       reverse_proxy sabnzbd:8080
   }
   ```
2. **Tailnet Redirect:** Add `/sabnzbd*` to the `@to_tailnet` matcher so direct HTTP requests redirect to the Tailscale HTTPS endpoint.

### 4.2 Site Registry (`homelab-site/site.yaml`)
- Add `sabnzbd: {}` under `services`.
- Add `sabnzbd` under `managed`.

---

## 5. Storage & Shared Download Layout

SABnzbd will use standard download paths inside `${DOWNLOADS_DIR}`:
- Complete downloads: `/downloads/complete`
  - Movies: `/downloads/complete/movies` (Category `movies`)
  - TV: `/downloads/complete/tv` (Category `tv`)
  - Music: `/downloads/complete/music` (Category `music`)
- Incomplete downloads: `/downloads/incomplete`

Because `${DOWNLOADS_DIR}` is mounted at `/downloads` across SABnzbd, Sonarr, Radarr, and Lidarr on the same underlying filesystem, moving completed downloads into the final media paths is an atomic hardlink / instant rename with no slow disk-to-disk duplication.

---

## 6. Zero-UI Provisioning Runbook (`homelab-plans/runbooks/d10-sabnzbd-setup.sh`)

Per `feedback-script-dont-click.md`, the owner never performs manual browser-based configuration. A runbook script in `homelab-plans/runbooks/d10-sabnzbd-setup.sh` handles initial bootstrap and media stack wiring.

### 6.1 Script Interface
```bash
bash d10-sabnzbd-setup.sh check
ssh -t jon-server 'bash /tmp/d10-sabnzbd-setup.sh apply'
```

### 6.2 Operations Performed
1. **Pre-flight & Secrets Verification**:
   - Confirms `/run/homelab-secrets/EWEKA_USERNAME`, `/run/homelab-secrets/EWEKA_PASSWORD`, `/run/homelab-secrets/SABNZBD_API_KEY`, and `/run/homelab-secrets/NZBGEEK_API_KEY` exist and are non-empty.
2. **SABnzbd Configuration Seeding**:
   - If `/srv/homelab/data/sabnzbd/sabnzbd.ini` does not exist or lacks the Eweka configuration:
     - Generates/patches `sabnzbd.ini` before SABnzbd starts, setting:
       - `[misc]`: `url_base = /sabnzbd`, `api_key = <SABNZBD_API_KEY>`, `nzb_key = <SABNZBD_API_KEY>`.
       - `[[news.eweka.nl]]`: `name = Eweka`, `host = news.eweka.nl`, `port = 563`, `ssl = 1`, `username = <EWEKA_USERNAME>`, `password = <EWEKA_PASSWORD>`, `connections = 50`, `enable = 1`.
       - `[categories]`: categories for `tv`, `movies`, `music` with relative/absolute completed paths.
     - Sets ownership to `${PUID}:${PGID}`.
     - This ensures SABnzbd launches with the setup wizard already complete.
3. **Prowlarr Usenet Indexer (NZBGeek) Provisioning**:
   - Reads `NZBGEEK_API_KEY` and adds NZBGeek as a Usenet indexer in Prowlarr via its REST API (`/api/v1/indexer`).
   - Enables sync profile so NZBGeek is automatically propagated to Sonarr, Radarr, and Lidarr.
4. **Download Client Automation in *arr Stack**:
   - Queries each *arr API (`sonarr:8989`, `radarr:7878`, `lidarr:8686`) using their local API keys.
   - Idempotently adds SABnzbd as a Usenet Download Client:
     - Host: `sabnzbd`
     - Port: `8080`
     - URL Base: `/sabnzbd`
     - API Key: `<SABNZBD_API_KEY>`
     - Category: `tv` (Sonarr), `movies` (Radarr), `music` (Lidarr).
   - Executes the test action to verify end-to-end communication.

---

## 7. Verification & Testing

1. **Static Validation**:
   - `scripts/dev/gate` passes cleanly in `homelab` (Compose validation, policy checks, line budgets).
   - `tests/test_policy.py` confirms `sabnzbd.compose.yml` meets all constraints.
2. **Release Rendering**:
   - `scripts/release/render.py` in synthetic site environment cleanly includes `sabnzbd`.
3. **Runbook Dry-Run**:
   - Runbook `check` mode validates secret presence and target service endpoints without side effects.
