# Modules

A module is one ordinary Compose service in
`modules/<tier>/<service>.yml`, where `<tier>` is `app` or `edge` and the
service name matches the file name. There is no second module format: Compose
expresses the service, while `homelab.*` labels carry the homelab contract.

The five rules are:

1. A file contains exactly one service and is in the `app` or `edge` tier.
2. Its image is written as `repository@sha256:<64 hex digits>`.
3. Every host bind is below `APP_DATA_DIR` or a media root from `site.env`.
   A repository file, site `files/` or `edge/` file is a Compose `config` with
   a `file:` source; the renderer inlines it for the release.
4. The service has an enabled Compose `healthcheck`, or a `homelab.probe`
   label such as `http://:<port><path>`. A disabled or null healthcheck does
   not count without the probe.
5. `homelab.state` is exactly `stateful` or `stateless`. Stateful binds are
   snapshotted before changes and are never restored automatically.

Other Compose features remain ordinary Compose. `depends_on` is documented
and validated, while applies select services explicitly with `--no-deps`.
