# Operations

Run these on the host. The controller runs `homelabctl reconcile` every 5 minutes from its systemd timer; state
lives in `/srv/homelab/state`, releases in `/srv/homelab/releases`.

## Everyday commands

```bash
homelabctl status                     # DEGRADED or ok, release pointers, what the next apply would change
homelabctl ack --reason "<reason>"    # clear DEGRADED after you have checked
homelabctl rollback <svc>             # stateful: restore its pre-deploy snapshot; then up from the previous release
homelabctl reconcile --now            # apply now, even in observe-only mode
```

## Updates

App-tier patch/minor Dependabot bumps merge and deploy on their own (usually within about 80 minutes). Majors and
edge images wait for you ([0014](decisions/0014-dependabot-automerge.md)).

Pause everything:

```bash
gh workflow disable dependabot-automerge.yml        # stop unattended merges
sudo touch /srv/homelab/state/observe-only          # stop applies on the host (status and pings keep working)
```

Resume with `gh workflow enable …` and `sudo rm /srv/homelab/state/observe-only`.

To change a service: edit its module (platform repo) or its override, `site.yaml` or config (site repo), and merge.
The release follows within minutes and the next tick applies it.

To stop a service and keep it stopped: `docker compose -p homelab stop <svc>`, then set `state: stopped` in
`site.yaml` so the next release agrees. The controller never starts a service you stopped.

## DEGRADED

A failed apply writes `/srv/homelab/state/DEGRADED` with the service, the snapshot ID and the next command, and
every tick sends a fail ping until you act.

1. `homelabctl status` and `docker compose -p homelab logs --tail 100 <svc>`.
2. Either roll back (`homelabctl rollback <svc>`) or merge a fix; a fix ships as a new release.
3. `homelabctl ack --reason "<what you did>"`.

## Edge

Edge (caddy, cloudflared) is never applied by the timer. Apply it yourself from the current release:

```bash
docker compose -p homelab-edge -f /srv/homelab/releases/current/edge.json up -d --no-deps caddy cloudflared
```

## New host

`host/install.sh` installs the controller, backup and the agent's tmpfs drop-in. Root's git refuses a checkout it
doesn't own, so run it from a clone owned by root, checked out at the release tooling SHA:

First create the service users, using the site's `PUID` and `PGID`:

```bash
sudo groupadd --system --gid <PGID> homelab
sudo useradd --system --uid <PUID> --gid homelab --no-create-home --shell /usr/sbin/nologin homelab
sudo useradd --system --gid homelab --no-create-home --shell /usr/sbin/nologin homelab-secrets
```

```bash
sudo git clone https://github.com/jon-robbins/homelab /tmp/homelab-install && sudo git -C /tmp/homelab-install checkout <sha>
sudo /tmp/homelab-install/host/install.sh --sha <sha> && sudo rm -rf /tmp/homelab-install
```

Three host steps are not automated:

1. The Infisical agent: install `config/systemd/homelab-infisical-agent.service` to `/etc/systemd/system/`, copy
   `config/infisical/templates/` to `/srv/homelab/infisical/templates/`, and write `/etc/infisical-agent/config.yaml`
   from `config/infisical/agent-config.yaml.example`, with the machine identity's credential files under
   `/etc/infisical-agent/auth/` (`0600`). Then `systemctl enable --now homelab-infisical-agent`.
2. Disk markers: `sudo touch <root>/.homelab-mounted` on each root the site lists in `marker_roots`, once the disk
   is mounted. The controller refuses to apply while a marker is missing.
3. The site sets `HOMELAB_SECRET_DIR=/run/homelab-secrets`.

## Break-glass

Restart one service without the controller, the network or GitHub:

```bash
docker compose -p homelab -f /srv/homelab/releases/current/app.json up -d --no-deps <svc>
```

## Rotate a secret

Change the value in Infisical, wait for the agent to render it (about a minute), then recreate the services that
mount it:

```bash
docker compose -p homelab -f /srv/homelab/releases/current/app.json up -d --no-deps --force-recreate <svc>
```

## Backups

`homelab-backup.timer` runs nightly at 04:15 ([0012](decisions/0012-backups.md)).

```bash
sudo systemctl start homelab-backup          # run one now
sudo homelab-backup --dry-run                # list what would be backed up
cat /var/lib/homelab-backup/last-success     # last good nightly run
cat /var/lib/homelab-backup/last-offsite     # last off-site copy
sudo restic snapshots --tag app-data         # with RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE set
```

`homelabctl rollback <svc>` restores a stateful service from its pre-deploy snapshot. The service's current data
is kept beside it as `<svc>.pre-restore-<timestamp>`.

## Alerts

healthchecks.io has two checks ([0013](decisions/0013-deadman-alerting.md)):

| Check | Fails when |
|---|---|
| controller | no tick for 20 minutes; DEGRADED; under 10% free on `/` or the media disk; a missing disk marker; Docker unreachable; a container start error |
| backup | no successful nightly run; a backup, copy or check error |

The fail ping's body names the cause.

## After a Docker Compose upgrade

A Compose upgrade can change every service's config hash, so the next tick would recreate every managed service.
Run `homelabctl status` first; if it lists services you didn't change, wait for a quiet moment and run
`homelabctl reconcile --now`.

## Upgrading the controller

From a checkout at the new commit: `sudo host/install.sh --sha "$(git rev-parse HEAD)"`. The previous version
stays in `tooling/` and `tooling/current` switches over. Changed systemd units need `--replace <unit>`.
