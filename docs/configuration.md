# Configuration

**Status: Current.** This page documents the public environment contract, not
a site deployment.

Compose reads the variables listed in [`.env.example`](../.env.example).
Use `scripts/ci/compose-config-check.sh` for a render with synthetic secret
files. Managed secret names remain blank in the example; runtime values belong
to a private secret manager. The public check does not require that manager.

Common non-secret inputs include `PUID`, `PGID`, `TZ`, `BASE_DOMAIN`,
`APP_DATA_DIR`, media example paths, and feature flags. Keep paths relative to
the checkout for off-host validation. The public Caddy configuration uses
`example.homelab.local`; it is a template, not an assigned hostname.

For service access see [network access](network-access.md). For route syntax see
[Caddy guide](caddy-guide.md). A host controller and signed runtime
configuration are **Planned**, not configuration features today.

