# Operations

Run these commands on the controller host. `status` reports release pointers
and service health; acknowledge a recovered degraded state explicitly.

```bash
homelabctl status
homelabctl ack --reason "<reason>"
homelabctl rollback <svc>
homelabctl apply --edge
homelabctl apply --edge --takeover
```

For a break-glass restart of one service:

```bash
docker compose -p homelab -f /srv/homelab/releases/current/app.json up -d --no-deps <svc>
```

For secret rotation, force recreation from the current release:

```bash
docker compose -p homelab -f /srv/homelab/releases/current/app.json up -d --no-deps --force-recreate <svc>
```
