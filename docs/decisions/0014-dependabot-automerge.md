# 0014: App-tier patch and minor updates merge and deploy unattended

- **Status:** Accepted (2026-10-20; amends the "owner merge is the approval" rule of [0004](0004-signed-full-state-releases.md));
  the edge-tier exclusion superseded by [0016](0016-edge-updater.md) (2026-10-25); the edge members amended by
  [0020](0020-traefik-edge.md) (2026-11-02): the edge images are now traefik and cloudflared, and Traefik minors
  wait for the owner
- **Code:** `.github/workflows/dependabot-automerge.yml`, `.github/dependabot.yml`
- **Related:** [0009](0009-image-trust.md), [operations](../operations.md)

## Decision

Dependabot image bumps for **app-tier** modules that are **patch or minor** updates merge automatically once CI is
green. The normal release and controller path then deploys them, usually within about 80 minutes.

Majors, edge-tier images (caddy, cloudflared), GitHub Actions and Python dependencies still wait for the owner.
*(Amended by [0016](0016-edge-updater.md) and [0020](0020-traefik-edge.md): the edge images are now traefik and
cloudflared; their patch and minor bumps merge unattended, but Traefik minors wait for the owner because
minors have flipped security defaults.)*

Exception (2026-10-25): Soularr's image (`mrusse08/soularr`) waits for the owner at any update type. It is one
person's Docker Hub image with no outside review, it holds the Lidarr and slskd API keys, and it is not
internet-facing, so an unattended bump carries the risk without the security-patch benefit.

Exception (2026-11-06): Dependabot ignores major Postgres bumps (`library/postgres`, `.github/dependabot.yml`). A
major image swap cannot start on a data directory written by the previous major, so it needs a dump and restore, not
a pin change. Merging the 16 to 18 bump on 2026-11-05 crash-looped Authentik's database until it was rolled back.
Minor and patch bumps within a major still flow as above; plan a major upgrade by hand.

Exception (2026-11-07): Dependabot ignores the legacy `v4-4.0.0.*-lsN` tags of `linuxserver/sonarr`. Dependabot
reads `v4-4.0.0.124-ls2` as version `4.4.0.0.124`, which ranks above the current `4.0.20.3014-ls2`, so it proposed
that 2023 build as an upgrade and automerge merged it (PR #128, reverted by #133). That build predates the fixes for
CVE-2026-30975 and CVE-2026-30976. The ignore is the range `>= 4.4.0.0.0, < 4.4.0.1`, the derived versions of that
series; no real 4.x build falls in it. An earlier floor of `< 4.0.17` (PR #134) never matched, because it was
compared against the derived `4.4...` number. Real newer builds still flow. Check the next weekly Dependabot run.

Pause: `gh workflow disable dependabot-automerge.yml`, or `sudo touch /srv/homelab/state/observe-only` on the host.

## Consequences

- Upstream security fixes reach the host without the owner, which matters most for internet-facing services
  ([0015](0015-exposure-policy.md)).
- A broken upstream minor release can reach the host. The controller's health gate, pre-change snapshot and
  DEGRADED alert are the safety net.

## Don't

- Don't extend automerge to the edge tier: a bad caddy image would cut off remote access.
  *(Historical Caddy rationale; the current Traefik policy is recorded in [0020](0020-traefik-edge.md).)*
