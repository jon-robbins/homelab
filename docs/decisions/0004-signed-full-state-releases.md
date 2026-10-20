# 0004: Signed, full-state releases on an event-driven trigger

- **Status:** Accepted (signing 2026-10-12; trigger 2026-10-15)
- **Code:** `.github/workflows/release.yml`, `scripts/release/`, `scripts/controller/feed.py`,
  `scripts/controller/admission.py`
- **Related:** [0005](0005-slim-controller.md), [0009](0009-image-trust.md), [architecture](../architecture/overview.md)

## Context

The first version deployed from a git checkout on the host, with a self-hosted runner holding a powerful token.
Anyone who could push could run code on the server, and nobody could say exactly what was running.

## Decision

A merge to any source repo produces **release N**: a signed `record.json` naming the commit of each source repo,
every image digest and the hash of the rendered Compose bundle. Releases are appended to the site repo's
`releases` branch (`records/<N>/`).

- **Signing:** Sigstore cosign, keyless. The release job signs with its GitHub OIDC identity; the host pins the
  exact workflow identity (`…/.github/workflows/release.yml@refs/heads/main`) and issuer, and verifies offline
  against a cached trusted root. No long-lived signing key exists.
- **Approval:** the owner's merge is the approval. The public repo relies on its `main` ruleset (PR required, no
  bypass), which the release job re-checks; private repos require an owner-merged PR.
- **Trigger:** `release.yml` runs after the post-merge image build succeeds; site and plugin repos dispatch it
  on push. An hourly schedule is only a backstop. Releases never depend on dev tooling.
- **Full state:** a release is the whole desired state, never a patch, so the controller only ever applies the
  newest one.

## Consequences

- One public Rekor transparency-log entry per release; Sigstore must be reachable to sign (not to verify).
- A bad release is fixed forward with a newer release; there is no "skip" or "revoke" list.

## Rejected

- Ed25519 key with key IDs and a trust map: key custody for one person is all cost.
- TUF: more roles and expiry upkeep for the same code size at this scale.

## Don't

- Don't deploy from a checkout or add a host-side path that bypasses the feed (break-glass uses the current
  release directory, which is already verified).
