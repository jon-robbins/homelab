# Troubleshooting

Organised by what you see. Each entry says where to look and the usual cause.

## A PR fails CI

| Message mentions | Cause | Fix |
|---|---|---|
| a module path and a rule | a module breaks one of the five rules | [modules](modules.md) |
| `bind`, `cap_add`, `network_mode`, `docker.sock`, `privileged` | Compose policy | [0008](decisions/0008-compose-policy.md); widen an allowlist only as its own change |
| `secret must be {file: …}` | secret declared any other way | [configuration](configuration.md#secrets) |
| budget, line count, `C901` | complexity budget | cut or simplify; [0011](decisions/0011-complexity-budgets.md) |
| public-readiness, private marker | a private value or token-shaped string in a tracked file | remove it; use example values |
| zizmor, actionlint | workflow security or syntax | fix the workflow; never ignore without a comment saying why |

Run `scripts/dev/gate` locally to reproduce.

## No release after a merge

1. Actions → `post-merge-build.yml` on `main`: did it succeed? `release.yml` only runs after it.
2. Actions → `release.yml`: the `verify` job prints the plan. Common causes: a site or plugin commit not merged
   through an owner-approved PR, an image digest that doesn't resolve, an image from a namespace not in
   `image-policy.json`, an expired `PRIVATE_INPUT_READ_TOKEN` or `SITE_FEED_TOKEN`.
3. For a site change: did `notify-release.yml` run in the site repo? An expired `RELEASE_DISPATCH_TOKEN` fails
   there. The hourly schedule catches up either way.

## The host did not pick up a release

```bash
homelabctl status
systemctl status homelab-controller.timer
journalctl -u homelab-controller --since -1h
```

| Seen | Cause |
|---|---|
| `observe-only` | `/srv/homelab/state/observe-only` exists; applies are paused |
| DEGRADED | a previous apply failed; see [operations](operations.md#degraded) |
| fetch error | `trust/feed.token` expired or lacks access to the site repo |
| signature or identity error | `trust/policy.json` names a different workflow identity, or `trust/trusted_root.json` is stale |
| service listed as "stopped by owner" | you stopped it; the controller won't start it |
| change not in plan | the service isn't in `managed`, or it is edge tier |

## A media service won't start after a reboot

`docker compose -p homelab ps -a` shows it exited with a mount error naming `.homelab-mounted`: the media disk
isn't mounted. Mount it (check `dmesg` and `/etc/fstab`), then start the service. Everything else keeps running
by design ([0007](decisions/0007-disk-markers.md)).

## A service can't authenticate to another

Usually a secret file is missing or stale.

1. `ls -l $HOMELAB_SECRET_DIR` on the host: is the file there, recent, readable by `PUID`?
2. `systemctl status homelab-infisical-agent` and its journal.
3. Force-recreate the service after the file is right ([operations](operations.md#rotate-a-secret)).

## A route through Caddy returns 404 or 403

- 404: no `handle` matched. Check the path prefix and `handle` versus `handle_path`
  ([ingress](ingress.md#caddy-routes)).
- 403: a `remote_ip` guard rejected the client. Check you're on the LAN or Tailscale.
- 502: the backend container is down or listening on another port; `docker compose -p homelab ps`.
- After changing the Caddyfile, the edge must be applied by hand ([operations](operations.md#edge)).

## Everything got recreated at once

A Docker Compose upgrade changed every config hash ([operations](operations.md#after-a-docker-compose-upgrade)).

## The disk is filling up

The controller's fail ping names the disk. Common causes: the download staging disk, Plex transcoder cache in
`APP_DATA_DIR/plex`, the local restic repository (retention is 30 days), and Docker images
(`docker image prune` removes unused ones).
