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
  traefik: {}                          # the tier comes from the module's directory
managed: [sonarr, autobrr, my-service] # what the controller may change
```

- `exposed` (optional) lists the ports that may listen on every interface, as `service:published-port/proto`, for
  example `jellyfin:8096/tcp`. Anything else on a wildcard address fails policy ([0015](decisions/0015-exposure-policy.md)).
- `services` decides what is in a release. `managed` decides what the controller applies; services not in it are
  only reported.
- `state: stopped` means "keep this created and stopped". The controller never starts a service the owner
  stopped, whatever `site.yaml` says.

## `site.env`

Non-secret values only. Modules may interpolate only keys defined here.

| Key | Meaning |
|---|---|
| `APP_DATA_DIR` | app data root, normally `/srv/homelab/data` |
| `MEDIA_DIR` | the one media directory (`downloads/`, `tv/`, `movies/`, `music/` below it); a bind root, normally a marker root |
| `MEDIA_BIND_IP`, `PEER_BIND_IP` | addresses for the media servers' direct ports and for the torrent and Soulseek peer ports; an IP, `0.0.0.0` means every interface and needs `exposed` |
| `MEDIA_HDD_PATH`, `MEDIA_NVME_PATH` | media disk mount points, for sites whose media spans disks |
| `MOVIES_DIR`, `TV_DIR`, `MUSIC_DIR`, `DOWNLOADS_DIR` | per-role paths for such sites, each below a bind root; modules don't use them, overrides and plugins may |
| `MEDIA_PLEX_PATH` | optional extra media tree for Plex |
| `PUID`, `PGID` | the user and group the services run as |
| `TZ` | time zone |
| `HOMELAB_SECRET_DIR` | where the Infisical agent writes secret files |
| `BASE_DOMAIN` | the domain Traefik serves |
| `TAILSCALE_IP` | the host's Tailscale address; Traefik binds 443 there |
| `QBT_PEER_PORT` | qBittorrent's peer port |

`.env.example` lists every key the catalog uses, with synthetic values.

## `overrides/<service>.yml`

A Compose fragment merged over one module, for things that are specific to one site (an extra media path, a GPU
device). Policy runs after the merge, so an override cannot add anything a module couldn't.

## `files/` and `edge/`

Config files that modules mount (`edge/traefik.yml`, `edge/config.yml` for cloudflared).
Modules refer to them as Compose `configs` with `file: site:<path>`; the renderer inlines their content into the
release.

## Secrets

Secret values never go in the site repo. Each module lists the secret names it needs under `secrets:`, and the
top-level entry is always `NAME: {file: ${HOMELAB_SECRET_DIR}/NAME}`. Platform and plugin repos ship Infisical
template skeletons with `@INFISICAL_PROJECT_SLUG@`; the site owns the real slug in
`config/infisical/project-slug`. On the host, `host/stamp-infisical-templates` writes stamped templates under
`/srv/homelab/infisical/templates/`, and the Infisical agent renders one file per name into `HOMELAB_SECRET_DIR`.

On the host that directory is `/run/homelab-secrets`: tmpfs, files `0640 homelab-secrets:homelab`, so secret values
never reach disk and are gone after a reboot until the agent renders them again. `host/install.sh` installs the
agent drop-in that creates the directory and waits for every rendered file (`host/wait-for-secrets`), and orders
Docker after the agent so containers never start without them. The site sets
`HOMELAB_SECRET_DIR=/run/homelab-secrets`.

Current names: `LIDARR_API_KEY`, `CLOUDFLARE_TOKEN`,
`SLSKD_SLSK_USERNAME`, `SLSKD_SLSK_PASSWORD` (the Soulseek account; slskd registers it on first login),
`SLSKD_WEB_PASSWORD` (slskd's web UI, user `slskd`), `SLSKD_API_KEY` (shared by slskd and Soularr).

Seerr needs no TMDB key (it ships a shared one). A site that wants its own adds `TMDB_API_KEY` to the `overseerr`
override as an ordinary secret (`secrets: [TMDB_API_KEY]` plus the top-level `{file: ...}` entry); the entrypoint uses
it when the file exists.

After rotating a secret, force-recreate the services that mount it
([operations](operations.md#rotate-a-secret)). See [0010](decisions/0010-secrets.md).
