# Caddy guide

**Status: Current.** The public Caddyfile is a safe template using
`example.homelab.local`; this page documents syntax, not live routes.

Use `handle /service*` when a backend expects its URL base to remain present.
Use `handle_path /service*` when the backend expects the prefix removed. Keep
routes in [`config/caddy/Caddyfile`](../config/caddy/Caddyfile), validate with
the public check, and do not add site names or access rules here.

WAN policy, private certificates, and deployment-specific routes are outside
this public repository. A host controller is **Planned**.

