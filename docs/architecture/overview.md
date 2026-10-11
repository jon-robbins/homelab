# Public platform architecture

This repository is the reusable, off-host portion of a Docker Compose
platform. It contains generic application code, Compose modules, and safe
defaults. A separate private site overlay supplies deployment choices such as
which services are enabled, where data lives, and how ingress is named. The
public tree must remain useful without knowing any real host, network, or
identity.

## Boundaries and execution model

The architecture has three deliberate boundaries:

1. Public code and generic worker routines are built and tested in isolation.
2. A single trusted controller renders and applies the selected Compose
   modules on the target host. The controller is the only component with
   authority to reconcile the private site overlay.
3. Optional private integrations register through extension interfaces. They
   are not imported by the public packages and are not part of the default
   public deployment.

CI/CD is quarantined from the site: CI may lint, render, test, and scan the
public tree, but it does not receive site credentials or mutate the controller.
The controller obtains runtime secrets out of band and exposes them to a
container as files under `/run/secrets/`. Public defaults use
`example.homelab.local`, relative `example-data` paths, and synthetic values.

## Two-service example

The smallest useful deployment has two fictional services on one Compose
network:

```yaml
services:
  hello:
    image: hashicorp/http-echo:1.0
    command: ["-text=hello from the platform"]
    environment:
      DEMO_API_TOKEN_FILE: /run/secrets/demo_api_token
    secrets: [demo_api_token]
  whoami:
    image: traefik/whoami:v1.10
    secrets: [demo_api_token]

secrets:
  demo_api_token:
    file: ./example-secrets/demo-api-token.txt
```

The two services share only the Compose network and the explicitly granted
secret. An ingress layer may route `/hello` to `hello` and `/whoami` to
`whoami`. The file can contain a value such as `synthetic-demo-token-only` for
local validation; it is an ignored fixture, not a production credential.
Replace these images with reviewed immutable digests before a real deployment.

## Execution model

CI validates YAML, Compose rendering, package tests, and boundary scans. Site
operators apply the private overlay separately. Runtime secrets are mounted
from an external secret manager or ignored local files; real secret values are
never put in Compose or documentation.

See [the threat model](threat-model.md) and [getting started](../getting-started.md).
