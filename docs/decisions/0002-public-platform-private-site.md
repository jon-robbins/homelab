# 0002: Public platform, private site repo, plugin repos

- **Status:** Accepted (2026-09-27; plugin model 2026-10-15)
- **Code:** whole repository; `tests/boundary/`, `scripts/ci/public-readiness.py`
- **Related:** [0001](0001-compose-modules.md), [plugins guide](../plugins.md), [configuration](../configuration.md)

## Context

The first version kept code, site values (domain, Tailscale IP, home paths) and credentials in one repository.
Its history could never be published, and site-specific shortcuts leaked into shared code.

## Decision

Three kinds of repository, with a one-way dependency rule:

| Repo | Holds | Never holds |
|---|---|---|
| `homelab` (this one, public) | module catalog, Compose policy, renderer, release workflow, controller, host scripts, first-party images, docs | site values, private names, plugin code |
| site repo (private) | `site.yaml`, `site.env`, `files/`, `overrides/`, `edge/`, code-free plugins, the signed release feed (`releases` branch) | code, generated files, secrets |
| plugin repo (private, optional) | code, image build and modules for services only one owner runs | site values |

Dependency rule: site → platform and plugins; plugin → platform contract; platform → nothing private.

## Consequences

- Public CI can validate everything with synthetic values; `public-readiness.py` and the boundary tests reject
  private IP ranges, home paths and token-shaped strings in tracked files.
- A second site would be a second site repo. The contract allows it; nothing builds it yet.
- The public repo was created from one fresh commit of a reviewed tree, because the earlier history contained
  private values.

## Don't

- Don't add a site-specific default to platform code "just for now"; put it in `site.env` or an override.
- Don't reference a plugin by name from `modules/`, `scripts/` or `host/`.
