# Getting started

Two paths:

- [Try it off-host](#try-it-off-host) in five minutes, with synthetic values and no containers.
- [Set up a real host](#set-up-a-real-host), from an empty Linux machine to services deployed by signed releases.

## Try it off-host

Requires Python 3.11+.

```bash
cp .env.example .env.local
bash scripts/ci/public-check.sh
```

This runs the documentation, boundary, CI and release tests and the public-readiness scan. It never starts a
container. To see what a site renders to:

```bash
python3 -m scripts.release.render examples/site
```

## Set up a real host

Read [architecture](architecture/overview.md) first; the steps below assume you know what a site repo, a release
and the controller are. Collect the [accounts](accounts.md) before you start.

### What you need

- A Linux host (Ubuntu 24.04 is what this is run on) with Docker Engine and the Compose plugin, `git`, Python
  3.11+, `restic`, `sqlite3`, and `cosign` on the `PATH`.
- Optional: an NVIDIA GPU with the NVIDIA Container Toolkit, or an Intel/AMD GPU at `/dev/dri`, for hardware
  transcoding in Plex or Jellyfin.
- One or more data disks for media, mounted at fixed paths.

### 1. Fork the platform

Fork this repo (or create your own copy), enable Actions, and add a `main` ruleset: pull request required, no
bypass. Some values still name the original owner; change them in your fork until they become settings:

| Where | What to change |
|---|---|
| `scripts/release/compose_policy.py` `FIRST_PARTY`, `config/release/approved-ghcr-destinations.json`, first-party `image:` lines in `modules/` | `ghcr.io/<you>/homelab/` |
| `scripts/release/reconcile.py`: `SITE_REPOSITORY`, `RELEASE_OWNERS`, default `GITHUB_REPOSITORY` | your site repo, your GitHub username, your platform repo (or set these as environment variables in `release.yml`) |
| `scripts/controller/reconcile.py` | the media mount checked for free space (`/mnt/media-hdd`) |
| `host/homelab-backup` | `REPO` (local restic repository), `VPN_ROOT`, and the per-service SQLite trimming cases |
| `scripts/release/compose_policy.py` `PROTECTED` | directories of unmanaged services on your host |

### 2. Create the site repo

Create a private repo with this layout (see [configuration](configuration.md) for every key):

```text
site.yaml        # which services run, which the controller manages
site.env         # non-secret values: paths, PUID/PGID, TZ, BASE_DOMAIN, TAILSCALE_IP
files/           # config files modules mount (e.g. a mounted config file)
overrides/       # per-service Compose fragments the policy allows
edge/Caddyfile   # your routes; start from config/caddy/Caddyfile
plugins/         # optional code-free plugins
.github/workflows/notify-release.yml
```

`notify-release.yml` runs `gh workflow run release.yml -R <you>/homelab -f mode=reconcile` on every push to
`main`, authenticated with `RELEASE_DISPATCH_TOKEN`. Create an empty `releases` branch; the release job writes
records there.

Start with a few services in `services:` and only one in `managed:`. Grow `managed` once the first one works.

### 3. Add the GitHub secrets

Create the tokens in [accounts](accounts.md#github-tokens) and set them on the platform and site repos. Merge
any small PR in the platform repo; `post-merge-build.yml` then `release.yml` should run and push `records/1` to
the site repo's `releases` branch.

### 4. Prepare the host

```bash
sudo tailscale up                          # note the Tailscale IP for site.env TAILSCALE_IP
sudo mkdir -p /srv/homelab/data
# For each media disk listed in site.yaml marker_roots, once it is mounted:
sudo touch /mnt/<disk>/.homelab-mounted
```

The two lines above plus `chattr +i` on the empty mount directory (with the disk unmounted) are enough.
See [0007](decisions/0007-disk-markers.md).

### 5. Secrets

1. In Infisical, create a project with one secret per name your enabled modules declare (look for `secrets:` in
   `modules/`; the full list is in `.env.example`).
2. Install the Infisical CLI on the host and a machine identity for it.
3. Install `config/infisical/agent-config.yaml.example` as `/etc/infisical-agent/config.yaml`, with its
   destination paths pointing at your `HOMELAB_SECRET_DIR`, and install
   `config/systemd/homelab-infisical-agent.service`. Remove the `commands:` reload entries; reloads were retired
   ([0010](decisions/0010-secrets.md)).
4. Check that one file per secret appears in `HOMELAB_SECRET_DIR`, readable by `PUID`.

### 6. Install the controller

On the host, from a checkout of your platform repo at the commit you want:

```bash
sudo host/install.sh --sha "$(git rev-parse HEAD)"
```

This creates `/srv/homelab/{tooling,trust,state,releases,data}`, installs the controller into
`tooling/<sha>`, installs and enables the controller and backup timers, and starts in **observe-only** mode.

Then fill `/srv/homelab/trust/` (root, mode `0700`):

| File | Content |
|---|---|
| `policy.json` | `{"schema": 1, "site_id": "<site_id from site.yaml>", "signer_identity": "https://github.com/<you>/homelab/.github/workflows/release.yml@refs/heads/main", "oidc_issuer": "https://token.actions.githubusercontent.com", "trusted_root": "/srv/homelab/trust/trusted_root.json", "feed_directory": "https://github.com/<you>/<site-repo>.git"}` |
| `trusted_root.json` | Sigstore's public trusted root (`targets/trusted_root.json` in the `sigstore/root-signing` repository); refresh it when Sigstore rotates keys |
| `feed.token` | the read-only feed token |
| `deadman.env` | `CONTROLLER_PING_URL=…` and `BACKUP_PING_URL=…` |
| `docker/config.json` | only if a plugin image is private |

For backups, set `/etc/homelab-backup/env` (`REPO`, `OFFSITE_REPO`), the restic passwords in
`/root/.config/restic/`, and initialise the repositories with `restic init`.

### 7. Edge

Apply Caddy (and cloudflared, if you use it) from the first release, then check that you can reach a route over
Tailscale:

```bash
docker compose -p homelab-edge -f /srv/homelab/releases/current/edge.json up -d --no-deps caddy
```

The current release exists after the first `homelabctl reconcile --now` (next step). Read
[ingress](ingress.md) and [security](security.md) before opening any router port.

### 8. First apply

```bash
homelabctl status                 # what the next apply would change
sudo homelabctl reconcile --now   # applies even in observe-only mode
homelabctl status
```

When applies look right, remove `state/observe-only` so the timer applies new releases by itself, and check the
healthchecks.io dashboard shows both checks green.

### 9. Before you rely on it

- Run one backup by hand (`sudo systemctl start homelab-backup`) and restore one service from it.
- Reboot once and confirm everything comes back and the controller pings.
- Walk through the [security checklist](security.md#checklist).
