# Modules

A module is one ordinary Compose service in `modules/<tier>/<service>.yml`, where `<tier>` is `app` or `edge`
and the service name matches the file name. There is no second module format: Compose expresses the service, and
`homelab.*` labels carry the homelab contract.

The rules:

1. A file contains exactly one service, named after the file, in the `app` or `edge` tier.
2. Its image is pinned by digest: `repository:tag@sha256:<64 hex>`. The tag is optional but keeps the pin readable
   and lets Dependabot propose bumps. Upstream images come from a namespace in `config/release/image-policy.json`;
   first-party images (`ghcr.io/jon-robbins/homelab/...`) are re-pinned by the release to the digest it built.
3. Every host bind is below one of the roots the site names in `bind_roots` (values from `site.env`). Files from a
   repository (`config/...`, the site's `files/...` and `edge/...`) are Compose `configs` with a `file:` source
   (`site:<path>` for the site repo); the renderer inlines their content and mode, so a release never points back
   into a git checkout. Secrets are `NAME: {file: ${HOMELAB_SECRET_DIR}/NAME}`, rendered by the Infisical agent on
   the host and never inlined.
4. The service has an enabled `healthcheck`, or, for images without a shell, a `homelab.probe` label such as
   `http://:8080/health` that documents the check.
5. `homelab.state` is exactly `stateful` or `stateless`. Stateful binds are snapshotted before changes and never
   restored automatically.

Variables are only `${NAME}`, with `NAME` defined in the site's `site.env` (no `${NAME:-default}`, no `$NAME`).

Where each rule runs: `scripts/release/modules.py` checks the file layout and variables (what Compose would
erase); `scripts/release/compose_policy.py` checks everything else on the rendered model, after
`docker compose config` has merged overrides and normalized the syntax. To render a site and see the result:

```bash
python3 -m scripts.release.render examples/site      # from the repository root
```

Other Compose features remain ordinary Compose. `depends_on` is validated by Compose itself; applies select
services explicitly with `--no-deps`.
