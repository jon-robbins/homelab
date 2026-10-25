# Configuration: the site contract

A site is a private repo holding values only. Nothing in it runs, and nothing in it is generated.
[`examples/site/`](../examples/site/) is a complete, synthetic example.

## `site.yaml`

```yaml
contract: 1
site_id: home                          # must match site_id in the host's trust/policy.json
bind_roots: [APP_DATA_DIR, MEDIA_HDD_PATH, MEDIA_NVME_PATH]   # site.env keys; binds must sit under these
marker_roots: [MEDIA_HDD_PATH, MEDIA_NVME_PATH]               # disks that carry .homelab-mounted
plugins:
  my-plugin: {repository: <owner>/<repo>}   # a plugin with code, in its own repo
  extras: {path: plugins/extras}            # a code-free plugin in this repo
services:
  sonarr: {}
  autobrr: {state: stopped}            # created by the controller, never started
  my-service: {from: my-plugin}
  caddy: {}                            # the tier comes from the module's directory
managed: [sonarr, autobrr, my-service] # what the controller may change
```

- `services` decides what is in a release. `managed` decides what the controller applies; services not in it are
  only reported.
- `state: stopped` means "keep this created and stopped". The controller never starts a service the owner
  stopped, whatever `site.yaml` says.

## `site.env`

Non-secret values only. Modules may interpolate only keys defined here.

| Key | Meaning |
|---|---|
| `APP_DATA_DIR` | app data root, normally `/srv/homelab/data` |
| `MEDIA_HDD_PATH`, `MEDIA_NVME_PATH` | media disk mount points |
| `MOVIES_DIR`, `TV_DIR`, `DOWNLOADS_DIR` | media role paths, each below a bind root (linuxserver convention: `/movies`, `/tv`, `/downloads` in containers) |
| `MEDIA_PLEX_PATH` | optional extra media tree for Plex |
| `PUID`, `PGID` | the user and group the services run as |
| `TZ` | time zone |
| `HOMELAB_SECRET_DIR` | where the Infisical agent writes secret files |
| `BASE_DOMAIN` | the domain Caddy serves |
| `TAILSCALE_IP` | the host's Tailscale address; Caddy binds 443 there |
| `QBT_PEER_PORT` | qBittorrent's peer port |

`.env.example` lists every key the catalog uses, with synthetic values.

## `overrides/<service>.yml`

A Compose fragment merged over one module, for things that are specific to one site (an extra media path, a GPU
device). Policy runs after the merge, so an override cannot add anything a module couldn't.

## `files/` and `edge/`

Config files that modules mount (`files/workers.yaml`, `edge/Caddyfile`, `edge/config.yml` for cloudflared).
Modules refer to them as Compose `configs` with `file: site:<path>`; the renderer inlines their content into the
release.

## Secrets

Secret values never go in the site repo. Each module lists the secret names it needs under `secrets:`, and the
top-level entry is always `NAME: {file: ${HOMELAB_SECRET_DIR}/NAME}`. On the host, the Infisical agent renders
one file per name into `HOMELAB_SECRET_DIR` from the templates in `config/infisical/templates/`.

On the host that directory is `/run/homelab-secrets`: tmpfs, files `0640 homelab-secrets:homelab`, so secret values
never reach disk and are gone after a reboot until the agent renders them again. `host/install.sh` installs the
agent drop-in that creates the directory and waits for every rendered file (`host/wait-for-secrets`), and orders
Docker after the agent so containers never start without them. The site sets
`HOMELAB_SECRET_DIR=/run/homelab-secrets`.

Current names: `TMDB_API_KEY`, `QBITTORRENT_USERNAME`, `QBITTORRENT_PASSWORD`, `SONARR_API_KEY`,
`RADARR_API_KEY`, `PROWLARR_API_KEY`, `OVERSEERR_API_KEY`, `LIDARR_API_KEY`, `CLOUDFLARE_TOKEN`,
`SLSKD_SLSK_USERNAME`, `SLSKD_SLSK_PASSWORD` (the Soulseek account; slskd registers it on first login),
`SLSKD_WEB_PASSWORD` (slskd's web UI, user `slskd`), `SLSKD_API_KEY` (shared by slskd and Soularr).

After rotating a secret, force-recreate the services that mount it
([operations](operations.md#rotate-a-secret)). See [0010](decisions/0010-secrets.md).
