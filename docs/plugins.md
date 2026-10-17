# Plugins

A plugin packages services specific to one homelab. Its directory contains:

```text
homelab-plugin.yaml  # contract: 1, name and description; image_namespace for code repos
README.md            # services, site.env keys and how to run them
modules/app/*.yml    # or modules/edge/*.yml
```

Code-free plugins live under the site as `plugins/<name>/` and are enabled
with `{path: plugins/<name>}`. A plugin with its own code is enabled with
`{repository: owner/repository}` and keeps its modules in that repository.
Code repositories also declare `image_namespace`; their images must come
from that namespace. All images, including upstream images, are digest
pinned.

Modules are discovered below `modules/<tier>/`; no service list is kept in
sync. They follow the module contract, may depend on platform modules, and
cannot reuse a platform service name. Platform modules never depend on a
plugin. Validation also requires the README to mention every shipped service
as a complete name, not merely as a substring.

See [`examples/plugin-hello`](../examples/plugin-hello/) and
[`examples/site`](../examples/site/) for a complete path-plugin example.
