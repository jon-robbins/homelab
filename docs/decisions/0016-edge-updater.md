# 0016: The edge tier updates itself, with rollback

- **Status:** Accepted (2026-10-25)
- **Supersedes:** the edge row and the first **Don't** of [0003](0003-tiers.md); the **Don't** of
  [0014](0014-dependabot-automerge.md)
- **Code:** `host/homelab-edge-update`, `host/install.sh`, `.github/workflows/dependabot-automerge.yml`
- **Related:** [0003](0003-tiers.md), [0005](0005-slim-controller.md), [0013](0013-deadman-alerting.md),
  [0014](0014-dependabot-automerge.md), [operations](../operations.md#edge)

## Context

[0003](0003-tiers.md) kept caddy and cloudflared owner-applied so that a broken release could not remove the path
used to fix it. In practice every edge bump waited for the owner to SSH in, including security fixes for the two
internet-facing services. And the path used to fix things is SSH over Tailscale, which is host tier: neither
Caddy nor cloudflared carries it. A bad edge image breaks the web UIs and the tunnel, not access to the host.

## Decision

1. `homelab-edge-update`, a host-tier timer separate from the controller, applies the current release's
   `edge.json` when it differs from the last one that came up healthy.
2. It pulls first; a failed pull changes nothing and is retried next run. It then recreates the edge services
   with `--no-deps` and waits for each to report healthy, which for these services means Caddy's admin API
   answers and the tunnel is connected.
3. If a service does not become healthy, it re-applies the last good `edge.json` (before the first good apply,
   the previous release's), and skips that release until a newer one arrives.
4. While the running edge differs from the release, the controller's existing "edge change pending" notice fails
   the deadman ping ([0013](0013-deadman-alerting.md)); that is the alert. No new alerting path.
5. Edge-tier Dependabot patch and minor bumps merge unattended, like the app tier ([0014](0014-dependabot-automerge.md)).
6. `observe-only` stops it, as it stops the controller.

## Consequences

- Edge security fixes deploy without the owner, usually within the same window as app updates.
- A bad edge release costs a few minutes of broken web UIs or tunnel, then a rollback and an alert.
- Edge recreation takes no snapshot. Caddy's state is its certificates, which a recreate does not touch.
- The controller still never touches the edge tier, and its line budget is unchanged.

## Don't

- Don't move this into the controller; keeping the edge path separate is what lets one fail without the other.
- Don't retry a rolled-back release on every run; it would flap the edge every five minutes.
- Don't roll back on a pull failure; nothing has changed yet.
