# Operations

Run these commands on the controller host. The controller runs `homelabctl reconcile` every 5 minutes from
its systemd timer; state lives in `/srv/homelab/state`, releases in `/srv/homelab/releases`.

App-tier patch/minor Dependabot bumps merge + deploy on their own (≤ ~1 h 20 min); majors and edge wait for the owner; pause = `gh workflow disable dependabot-automerge.yml`, or `sudo touch /srv/homelab/state/observe-only`.

```bash
homelabctl status                     # DEGRADED or ok, release pointers, what the next apply would change
homelabctl ack --reason "<reason>"    # clear DEGRADED after you have checked
homelabctl rollback <svc>             # stateful: restore its pre-deploy snapshot; then up from the previous release
homelabctl reconcile --now            # apply now, even in observe-only mode
```

Edge (caddy, cloudflared) is never applied by the timer. Apply it yourself from the current release; remove a
same-named legacy container first if one exists:

```bash
docker rm -f caddy cloudflared        # only when taking over from the legacy stack
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

For a break-glass restart of one service:

```bash
docker compose -p homelab -f /srv/homelab/releases/current/app.json up -d --no-deps <svc>
```

For secret rotation, force recreation from the current release:

```bash
docker compose -p homelab -f /srv/homelab/releases/current/app.json up -d --no-deps --force-recreate <svc>
```

After a Docker Compose upgrade, run `homelabctl status` before the next apply: a Compose upgrade can change every
service's config hash, so the next tick would recreate every managed service. If `status` lists services you didn't
change, wait for a quiet moment and run `homelabctl reconcile --now`.
