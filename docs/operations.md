# Operations

Run these commands on the controller host. The controller runs `homelabctl reconcile` every 5 minutes from
its systemd timer; state lives in `/srv/homelab/state`, releases in `/srv/homelab/releases`.

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

Before adopting a service (migration only, removed in M7), compare the running container with the release:

```bash
python3 -m scripts.migration.shadow --release /srv/homelab/releases/<seq> --legacy-data <legacy data dir> [svc...]
```
