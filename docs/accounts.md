# Accounts and external services

What you need before setting up a host, what each one is for, and where its credential ends up.

## Required

| Service | Used for | Credential | Stored in |
|---|---|---|---|
| **GitHub** (free plan is enough) | the platform repo (your fork, public), the site repo (private), Actions for CI and releases, GHCR for first-party images | see [GitHub tokens](#github-tokens) | GitHub Actions secrets; host `trust/` |
| **Infisical** (cloud free tier or self-hosted) | runtime secret values (API keys, passwords, tokens) | a machine identity for the host's Infisical agent | `/etc/infisical-agent/` on the host |
| **Tailscale** (free personal plan) | private remote access to admin UIs; Caddy binds the host's Tailscale address | device login on the host | `/var/lib/tailscale` |
| **A domain name** | HTTPS hostnames for Caddy | none | `BASE_DOMAIN` in `site.env` |
| **DNS provider with an API** (Cloudflare is what the Caddy image includes) | ACME DNS-01 certificates, so Caddy gets real TLS certificates without opening port 80 to the internet | API token scoped to *Zone:DNS:Edit* on that one zone | Infisical → `CLOUDFLARE_TOKEN` |
| **healthchecks.io** (free plan) | deadman alerts for the controller and backups | two ping URLs | `/srv/homelab/trust/deadman.env` |

## Optional

| Service | Used for | Notes |
|---|---|---|
| **Cloudflare Tunnel** | publishing a *web app* (for example Seerr) to the internet without opening a router port | Cloudflare's terms do not allow streaming video through the tunnel, so it is not an option for Plex or Jellyfin. See [ingress](ingress.md). |
| **Google Drive** (or any rclone/restic backend) | off-site copy of the nightly backup | create your own Google OAuth client for rclone; the shared default client hits quota limits. Use `drive.file` scope. |
| **Telegram** (or email) | where healthchecks.io sends alerts | configured inside healthchecks.io, not on the host |
| **Plex account** | Plex server claim and remote access | sign in from the Plex web UI on first start; homelab stores no Plex token |
| **TMDB** | metadata lookups for the worker that reconciles Seerr requests | `TMDB_API_KEY` in Infisical |
| **Indexers / trackers** | Prowlarr sources | configured inside Prowlarr; not part of this repo |

## GitHub tokens

All are fine-grained personal access tokens limited to the named repository. Give each the smallest permission
listed and an expiry you will notice.

| Name | Where it is set | Repository | Permission | Why |
|---|---|---|---|---|
| `PRIVATE_INPUT_READ_TOKEN` | platform repo → Actions secret (environment `production-release`) | site repo and each plugin repo | Contents: read, Pull requests: read | the release job reads the site and plugins and checks their merges |
| `SITE_FEED_TOKEN` | platform repo → Actions secret (environment `production-release`) | site repo | Contents: read and write | the release job appends `records/<N>` to the `releases` branch |
| `RELEASE_DISPATCH_TOKEN` | site repo (and each plugin repo) → Actions secret | platform repo | Actions: read and write | `notify-release.yml` starts a release after a site or plugin merge |
| `feed.token` | host: `/srv/homelab/trust/feed.token` | site repo | Contents: read | the controller fetches the signed feed |
| GHCR read token | host: `/srv/homelab/trust/docker/config.json` | — | `read:packages` (classic token) | only needed if a plugin image is private |

The platform repo's `main` branch needs a ruleset that requires a pull request and allows no bypass; the release
job checks it on every run.

## What is *not* needed

- No container registry account beyond GitHub.
- No cloud VM. Everything runs on one Linux host.
- No public IP or port forwarding for the platform itself. Port forwards are only for services you decide to
  expose (see [security](security.md#exposure-policy)).
