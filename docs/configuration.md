# Configuration

**Status: Current.** This page documents the public environment contract, not
a site deployment.

Modules interpolate only variables declared by the site contract. The catalog
test uses synthetic values from `tests/modules/site/site.env`. Managed secret
names remain blank in the example; runtime values belong to a private secret
manager.

Common non-secret inputs include `PUID`, `PGID`, `TZ`, `BASE_DOMAIN`,
`APP_DATA_DIR`, media example paths, and feature flags. Keep paths relative to
the checkout for off-host validation. The public Caddy configuration uses
`example.homelab.local`; it is a template, not an assigned hostname.

For service access see [network access](network-access.md). For route syntax see
[Caddy guide](caddy-guide.md). A host controller and signed runtime
configuration are **Planned**, not configuration features today.
