# 0014: App-tier patch and minor updates merge and deploy unattended

- **Status:** Accepted (2026-10-20; amends the "owner merge is the approval" rule of [0004](0004-signed-full-state-releases.md));
  the edge-tier exclusion superseded by [0016](0016-edge-updater.md) (2026-10-25)
- **Code:** `.github/workflows/dependabot-automerge.yml`, `.github/dependabot.yml`
- **Related:** [0009](0009-image-trust.md), [operations](../operations.md)

## Decision

Dependabot image bumps for **app-tier** modules that are **patch or minor** updates merge automatically once CI is
green. The normal release and controller path then deploys them, usually within about 80 minutes.

Majors, edge-tier images (caddy, cloudflared), GitHub Actions and Python dependencies still wait for the owner.

Exception (2026-10-25): Soularr's image (`mrusse08/soularr`) waits for the owner at any update type. It is one
person's Docker Hub image with no outside review, it holds the Lidarr and slskd API keys, and it is not
internet-facing, so an unattended bump carries the risk without the security-patch benefit.

Pause: `gh workflow disable dependabot-automerge.yml`, or `sudo touch /srv/homelab/state/observe-only` on the host.

## Consequences

- Upstream security fixes reach the host without the owner, which matters most for internet-facing services
  ([0015](0015-exposure-policy.md)).
- A broken upstream minor release can reach the host. The controller's health gate, pre-change snapshot and
  DEGRADED alert are the safety net.

## Don't

- Don't extend automerge to the edge tier: a bad caddy image would cut off remote access.
