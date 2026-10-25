# 0003: Host, edge, app and unmanaged tiers

- **Status:** Accepted (2026-10-15); the edge row and the first **Don't** superseded by
  [0016](0016-edge-updater.md) (2026-10-25)
- **Code:** `modules/app/`, `modules/edge/`, `host/`, `scripts/controller/`
- **Related:** [0005](0005-slim-controller.md), [operations](../operations.md)

## Context

In the first version Caddy was "an ordinary managed application", so a bad release could take down the only
way in. A boot orchestrator and a mount guard tried to sequence everything and failed in ways that were hard
to see.

## Decision

| Tier | Members | Changed by |
|---|---|---|
| host | Tailscale, controller and backup timers, Infisical agent, deadman pings | systemd units installed by `host/install.sh` |
| edge | caddy, cloudflared (Compose project `homelab-edge`) | the owner, by hand, from the current release |
| app | everything else (Compose project `homelab`) | the controller, per release |
| unmanaged | anything else on the host (for example a VPN in its own directory) | never this project |

## Consequences

- A broken release cannot remove the path you'd use to fix it: edge changes are reported, never applied.
- Unmanaged services are outside the controller's reach. The Compose policy rejects mounts into their
  directories, and the backup script may copy them read-only.

## Don't

- Don't let the controller apply edge changes. `apply --edge` was built and deliberately cut (2026-10-17).
- Don't add a boot-ordering mechanism; restart policies and disk markers ([0007](0007-disk-markers.md)) cover it.
