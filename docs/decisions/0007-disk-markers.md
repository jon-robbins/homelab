# 0007: Disk markers instead of mount ordering

- **Status:** Accepted (2026-10-15)
- **Code:** `scripts/release/render.py` (marker mounts), `scripts/controller/reconcile.py` (step 3)
- **Related:** [0003](0003-tiers.md), [troubleshooting](../troubleshooting.md)

## Context

When a media disk failed to mount, services wrote into the empty mount point on the root disk and filled it.
The first fix, `RequiresMountsFor=` on Docker plus a boot orchestrator, meant one dead disk took down every
container, including the VPN and the way in.

## Decision

- Each media disk carries a `.homelab-mounted` file at its root.
- Every service that binds a media root also binds that marker read-only with `create_host_path: false`
  (added by the renderer for roots listed in `site.yaml` `marker_roots`). If the disk is missing, the marker is
  missing and Docker refuses to start that container.
- The empty directory underneath each mount point is `chattr +i`, so nothing can write to the root disk there
  or recreate the marker.
- The controller checks every marker each tick and fails the deadman ping naming the missing disk.

## Consequences

A dead disk stops only the services that use it, loudly, within one tick. Everything else, including Tailscale
and the edge, keeps running.

## Don't

- Don't add `RequiresMountsFor=` or any other Docker-wide mount dependency.
- Don't remove the `create_host_path: false` on marker mounts; without it Docker recreates the marker on the
  root disk and the protection silently disappears.
