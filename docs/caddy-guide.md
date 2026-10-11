# Caddy Routing Guide

## Public and private site boundary

`config/caddy/Caddyfile` is the single tracked public source of truth. It is
safe to publish because it uses a synthetic default domain, loopback-only
access controls, and reusable service names. Site-specific domains, network
ranges, credentials, and other deployment details belong behind the private
site boundary.

To configure a private site, copy `caddy/Caddyfile.example` from the private
`homelab-site` repo to the ignored runtime path, provide deployment values out
of band, set `SITE_CADDYFILE`, and explicitly include the private site
override. The public Compose stack never selects that override implicitly.

## Public route contract

The public Caddyfile exposes only the following generic routes:

- `/sonarr`, `/radarr`, `/prowlarr`, `/readarr`, and `/bazarr` preserve their
  path prefixes for applications configured with matching URL bases.
- `/overseerr`, `/qbittorrent`, and `/autobrr` strip the prefix before proxying.
- `/dashboard` and `/autobrr` require loopback access; other requests receive
  an explicit unauthorized response.
- Unknown routes receive `404 Not Found`.
- The `:80` listener provides the same generic application routes without
  site-specific TLS or network configuration.

Keep this guide aligned with `config/caddy/Caddyfile`. Do not add real
domains, private paths, host-specific backends, network ranges, credentials,
or deployment-only TLS instructions to this tracked document.

## Adding a public route

1. Add a generic service route to `config/caddy/Caddyfile`.
2. Use `handle` when the upstream needs its path prefix and `handle_path` when
   the prefix should be removed.
3. Keep access controls explicit and retain the final not-found handler.
4. Add or update a focused boundary test before committing.

For private routes, update the ignored runtime Caddyfile through the private
site workflow and keep deployment-specific values out of this guide.

## Validation

Run the focused boundary suite and validate both tracked examples with a
host-compatible Caddy 2.x binary. Use synthetic environment values for the
private example; validation must not require a live deployment.

```bash
python3 -m unittest -v tests.boundary.test_pr02_task4_caddy
caddy validate --config config/caddy/Caddyfile --adapter caddyfile
caddy validate --config "$SITE_CADDYFILE" --adapter caddyfile
```

Do not apply Compose changes, reload a live service, or publish private
configuration as part of these checks.
