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

App- and edge-tier patch/minor Dependabot bumps merge and deploy on their own (usually within about 80 minutes).
Majors, Traefik minors and Soularr's image wait for you ([0014](decisions/0014-dependabot-automerge.md),
[0016](decisions/0016-edge-updater.md)).

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
every tick sends a fail ping until you act. A candidate release with a planning failure (such as a missing stateful
data directory) also stops before image pulls or snapshots, writes the reason to `DEGRADED`, and sends a fail ping
immediately. `homelabctl status` shows the blocked release and condition. Once the condition is fixed, run
`homelabctl ack --reason "<what you fixed>"` and then `homelabctl reconcile --now`. If a failed Compose
start left a managed container in `Created` state, the next signed release starts and checks it when the site
requests it running and Docker reports no start error, even if its config hash already matches. An `Exited`
container still counts as stopped by the owner.

1. `homelabctl status` and `docker compose -p homelab logs --tail 100 <svc>`.
2. Either roll back (`homelabctl rollback <svc>`) or merge a fix; a fix ships as a new release.
3. `homelabctl ack --reason "<what you did>"`.

## Edge

The edge tier (Traefik and cloudflared) is applied by its own timer, `homelab-edge-update`, never by the
controller ([0016](decisions/0016-edge-updater.md)). Every five minutes it applies the current release's `edge.json`
if it changed, waits for its services to be healthy, and otherwise puts the last good one back and skips that release
until a newer one. It applies exactly the services in `edge.json`, so a service a release drops is removed. While
the running edge differs from the release, the controller shows "edge change pending" as a notice. The deadman ping
does not fail on it. A rollback exits 1, so `homelab-edge-update.service` shows as failed until the next timer run,
which skips the release and exits 0. The journal records `edge: rolled back`.

- Last run: `journalctl -u homelab-edge-update -n 20`.
- After a rollback: fix forward with a new release, or apply the skipped one by hand once you know why it failed
  (`sudo rm /srv/homelab/state/edge-failed`, then `sudo systemctl start homelab-edge-update`).
- To apply by hand, as before:

```bash
docker compose -p homelab-edge -f /srv/homelab/releases/current/edge.json up -d --no-deps --remove-orphans
```

### Proxy cutover (old proxy to Traefik) ([0020](decisions/0020-traefik-edge.md))

These commands are a draft: rehearse the whole sequence on a disposable Docker host first, then obtain the owner's
operational approval; run them on a live host only in the approved window. Variables: `<prev-release>` the deployed
release before the swap, `<candidate>` the approved signed release number, `<approved-sha>`
the sha256 of the intended `edge.json`, `<old-image>@sha256:<digest>` the pinned proxy image the previous `edge.json`
ran.

1. Save recovery material and record current pause/timer state (whether `observe-only` already exists, whether
   `dependabot-automerge.yml` and the two timers are already off) so you can restore exactly that:

   ```bash
   sudo mkdir /srv/homelab/state/cutover
   sudo cp /srv/homelab/releases/<prev-release>/edge.json /srv/homelab/state/cutover/prev-edge.json
   docker pull <old-image>@sha256:<digest>   # before the outage window, while it is still available
   ```

   Retain the previous verified signed record and bundle as well as this model. Record both certificate-store bind
   paths and preserve their contents through the acceptance window; never run `down -v` or prune these resources.

2. Pause automation, then let in-flight work finish; do not interrupt running units. The updater checks `observe-only`
   only on entry, so an apply already running still finishes. Wait until both services report `inactive`:

   ```bash
   gh workflow disable dependabot-automerge.yml
   sudo touch /srv/homelab/state/observe-only
   sudo systemctl stop homelab-controller.timer homelab-edge-update.timer
   systemctl is-active homelab-controller.service homelab-edge-update.service   # wait for "inactive"
   ```

3. Land the candidate deliberately. There is no stage-only command: plain `reconcile` fetches, verifies and
   materialises a release, then refuses to advance `current` while `observe-only` exists. `homelabctl reconcile --now`
   intentionally overrides the pause and applies app-tier changes (never the edge) with the timers above stopped.
   Hold release publication (including manual dispatches and merges) throughout admission. Materialise under the pause:

   ```bash
   sudo homelabctl reconcile
   cmp /srv/homelab/releases/current/app.json /srv/homelab/releases/<candidate>/app.json
   homelabctl status
   ```

   For this proxy-only procedure, `cmp` must exit zero and status must show no app recreates, stopped-service creates
   or failures. Status describes the current model, so it is not a candidate preview: the byte comparison is required.
   If app models differ, stop and separately review the app deployment. Check the materialised candidate's record
   contains both approved source commits and its edge hash matches before the override:

   ```bash
   sudo homelabctl reconcile --now
   homelabctl status
   ```

   Require `current` to advance to exactly `<candidate>` without DEGRADED. Stop on a different release or an unexpected
   app change; do not touch the proxy. There is no CLI option to pin `--now` to a release, hence the publication hold.

4. Verify before the outage: `current` names the intended release,
   `sha256sum /srv/homelab/releases/current/edge.json` equals `<approved-sha>`, and the release records both intended
   commits — the platform and site halves must land as one signed candidate, because an intermediate pairing can fail
   rendering once the old module is removed.

5. Apply by hand with the updater's own flags — its unit does nothing while `observe-only` is set:

   ```bash
   docker compose -p homelab-edge -f /srv/homelab/state/cutover/prev-edge.json stop <old-proxy-service>
   docker compose -p homelab-edge -f /srv/homelab/releases/current/edge.json up -d --no-deps --wait --wait-timeout 180 --remove-orphans
   ```

   Stopping the old proxy first releases its ports and starts the accepted outage. Expected: it is removed as an
   orphan; Traefik and cloudflared come up healthy inside the timeout. Mirror a configured `EDGE_HEALTH_TIMEOUT`
   instead of the default 180 when applicable. Verify both certificate stores survive apply, rollback and cleanup.

6. Accept with real requests, not container lists: tailnet TLS, LAN HTTP and TLS on the exposed pairs, the tunnel
   origin, Authentik group allow and deny, forged `Remote-User` and `X-Authentik-*` headers stripped, representative
   upstream paths, and each store holding a real key and chain. On any failure — including a healthy apply whose
   routes are wrong — roll back by hand from the saved model while automation stays paused (under the updater a
   healthy-but-wrong apply overwrites `edge-good.json`; the saved copy is the rollback either way):

   ```bash
   docker compose -p homelab-edge -f /srv/homelab/releases/current/edge.json stop traefik
   docker compose -p homelab-edge -f /srv/homelab/state/cutover/prev-edge.json up -d --no-deps --wait --wait-timeout 180 --remove-orphans
   ```

   Verify the old proxy's routes answer again. Stop conditions: if the rollback itself fails, or there is no good
   model, stop, keep the saved image and model, recover by hand in break-glass style, and do not unpause.

7. Only after the candidate passes every acceptance check, record it as good before unpausing:

   ```bash
   sudo install -m 0644 /srv/homelab/releases/current/edge.json /srv/homelab/state/edge-good.json
   ```

   Inspect `state/edge-failed`: remove it only if its contents equal the accepted edge model's SHA-256. A marker for
   another release needs investigation and is never overwritten. After rollback, `current` still names the rejected
   model, so a resumed timer would apply it again: keep `observe-only` set, and record that model's hash so a later
   unpause skips it even after the pause is cleared. The manual apply never writes the marker, so write it when none
   exists:

   ```bash
   [[ -s /srv/homelab/state/edge-failed ]] ||
     sudo tee /srv/homelab/state/edge-failed >/dev/null <<<"$(sudo sha256sum /srv/homelab/releases/current/edge.json | cut -d' ' -f1)"
   ```

   Do not copy the rejected current model into `edge-good.json`, and do not clear its failed hash to retry unattended.

   Keep `observe-only` set until a corrected signed release is staged after a rollback; otherwise the restarted timer
   immediately reapplies the rejected candidate. Restore only what step 1 recorded — never clear an owner's earlier
   pause or re-enable an already-disabled workflow — then start the timers and watch one updater run
   (`journalctl -u homelab-edge-update -n 20`): it must apply nothing, since `edge-good.json` now equals current.
   After rollback, repeat this procedure with a corrected signed candidate before resuming. Close the rollback window
   explicitly before removing any saved material; preserve certificate stores and an image still used by a container.

## New host

`host/install.sh` installs the controller, the edge updater, backup and the agent's tmpfs drop-in. Root's git
refuses a checkout it doesn't own, so run it from a clone owned by root, checked out at the release tooling SHA:

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

1. The Infisical agent: install `config/systemd/homelab-infisical-agent.service` to `/etc/systemd/system/`. Stamp
   platform (and plugin) Infisical template skeletons with the site slug into `/srv/homelab/infisical/templates/`:

   ```bash
   sudo host/stamp-infisical-templates \
     --slug-file /path/to/site/config/infisical/project-slug \
     --from config/infisical/templates \
     --from /path/to/homelab-plugins/config/infisical/templates \
     --to /srv/homelab/infisical/templates
   ```

   Write `/etc/infisical-agent/config.yaml` from `config/infisical/agent-config.yaml.example`, with the machine
   identity's credential files under `/etc/infisical-agent/auth/` (`0600`). Then
   `systemctl enable --now homelab-infisical-agent`.
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
