# Plugins

A plugin packages homelab-specific services. A code-free plugin lives under
`homelab-site/plugins/<name>/`; a code plugin is enabled by its repository.
Both contain `homelab-plugin.yaml`, `README.md`, and modules below
`modules/app/` or `modules/edge/`.

The README must describe every service and its `site.env` keys. Plugin service
names cannot collide with platform modules. Plugins may depend on platform
modules, but platform modules never depend on plugins. Repository-owned images
must come from that repository's declared namespace and, like upstream images,
are pinned by digest.

See [`examples/plugin-hello`](../examples/plugin-hello/) and
[`examples/site`](../examples/site/) for a complete path-plugin example.
