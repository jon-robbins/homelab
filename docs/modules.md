# Modules

A module is one Compose service in `modules/app/<service>.yml` or
`modules/edge/<service>.yml`. Its service name must match the file name.

Every module must use a digest-pinned image, keep bind sources below the
`APP_DATA_DIR` or media roots declared by `site.env`, and provide either a
Compose `healthcheck` or a `homelab.probe` label. It must also declare
`labels.homelab.state` as `stateful` or `stateless`. Repository files belong
in Compose `configs` with a `file:` source; they are inlined when rendered.

The rest of the file is ordinary Compose. `depends_on` is documentation and
validation; platform calls explicitly select services with `--no-deps`.
