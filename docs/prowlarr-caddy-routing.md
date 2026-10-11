# Prowlarr and Caddy routing

**Status: Current template guidance.** When a service is configured with a URL
base such as `/prowlarr`, preserve that prefix with `handle`; use
`handle_path` only when the backend expects the prefix removed.

The public Caddyfile is the source of truth for its example routes. Validate
changes with [the public check](getting-started.md). Service data, live indexer
configuration, and external hostnames are private deployment inputs. A signed
release/controller path is **Planned**.

