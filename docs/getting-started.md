# Getting started off-host

This guide validates the generic platform without a production host or private
site overlay. Every value below is disposable.

## Prerequisites

- Docker Compose v2
- Python 3.11 or newer for boundary tests

## Render the example

Create the synthetic secret fixture used by the two-service example:

```bash
mkdir -p example-secrets
printf '%s\n' 'synthetic-demo-token-only' > example-secrets/demo-api-token.txt
chmod 600 example-secrets/demo-api-token.txt
```

The example in [the architecture overview](architecture/overview.md) mounts
that file as `/run/secrets/demo_api_token` for fictional `hello` and `whoami`
services. Do not substitute a real credential.

```bash
cp .env.example .env.local
docker compose --env-file .env.example config --quiet
python3 -m unittest -v tests.boundary.test_pr02_task4_caddy \
  tests.boundary.test_pr02_task5_compose \
  tests.boundary.test_pr02_task6_secrets \
  tests.boundary.test_pr02_task7_history \
  tests.boundary.test_pr02_task8_docs
```

The repository Compose files use only relative example-data paths and
`example.homelab.local`. Rendering validates configuration; it does not apply
a site overlay or start containers.

## Site deployment boundary

Copy the private site overlay only into a private deployment checkout. Set its
runtime paths, ingress, and access controls through private configuration or a
secret manager. Never replace the public example values with real values and
commit them.
