# Notes for coding agents

Read this before changing anything. `homelab` is a public product that guides people through setting up a secure,
fully wired media server on their own Linux machine. Keep the security core small; the wizard is separate, with its own
budget. The owner also uses this repository to publish signed releases for the owner's host.

## Orientation (read in this order)

1. [`docs/architecture/overview.md`](docs/architecture/overview.md): the system on one page.
2. [`docs/decisions/`](docs/decisions/README.md): why it is shaped this way. Each record lists the code it governs
   and the changes that were already rejected.
3. The guide for the area you are touching: [modules](docs/modules.md), [plugins](docs/plugins.md),
   [configuration](docs/configuration.md), [operations](docs/operations.md), [security](docs/security.md),
   [development](docs/development.md).

## Invariants (breaking one needs a new decision record first)

- One service per Compose file in `modules/<tier>/<service>.compose.yml`; no second description format. ([0001])
- Nothing private in this repo: no real domains, private IPs, home paths, plugin names or secrets. ([0002])
- New users set up a host through the guided local path and own a generated local site directory. The owner's site
  repository and release pipeline are for the owner's host only. A host belongs to exactly one setup mode; setup must
  refuse a host claimed by the other mode. ([0002], [0018])
- The controller never applies the edge tier (its own updater does, with rollback), never starts a service the
  owner stopped, always uses `--no-deps`, and never restores data on its own. ([0003], [0005], [0016])
- Only signed releases from `release.yml` on `main` run through the owner's pipeline. The owner's controller has no
  deploy-from-checkout path. Local setup is a separate, user-invoked path for stranger-owned hosts. ([0004], [0018])
- Every image is digest-pinned; every Action is SHA-pinned by tooling, not by hand. ([0009])
- Secrets are files from `${HOMELAB_SECRET_DIR}`; never in `environment:`, `site.env` or a release. ([0010])
- Line budgets in `config/ci/budgets.json` and `C901 <= 10`. Over budget means cut a feature, never compress code
  and never raise the budget in the same change. ([0011])
- The wizard is a separate entity in `setup/`, with its own 2,000-line budget. It may call platform interfaces; nothing
  in `scripts/`, `host/` or `modules/` may depend on it. ([0011], [0018])
- A published port binds an explicit interface unless it is on the exposure allowlist. ([0015], proposed)

[0001]: docs/decisions/0001-compose-modules.md
[0002]: docs/decisions/0002-public-platform-private-site.md
[0003]: docs/decisions/0003-tiers.md
[0004]: docs/decisions/0004-signed-full-state-releases.md
[0005]: docs/decisions/0005-slim-controller.md
[0009]: docs/decisions/0009-image-trust.md
[0010]: docs/decisions/0010-secrets.md
[0011]: docs/decisions/0011-complexity-budgets.md
[0015]: docs/decisions/0015-exposure-policy.md
[0016]: docs/decisions/0016-edge-updater.md
[0018]: docs/decisions/0018-local-setup-path.md

## Where to look when something breaks

| Symptom | Start here |
|---|---|
| A PR's CI fails on policy | `scripts/release/compose_policy.py` error path → [modules](docs/modules.md) rules |
| No new release after a merge | Actions tab: `post-merge-build.yml` then `release.yml`; [troubleshooting](docs/troubleshooting.md#no-release-after-a-merge) |
| Host did not deploy a release | `homelabctl status` on the host; [troubleshooting](docs/troubleshooting.md#the-host-did-not-pick-up-a-release) |
| DEGRADED alert | [operations](docs/operations.md#degraded) |
| A media service won't start after reboot | disk markers, [0007](docs/decisions/0007-disk-markers.md) |
| Service can't read a secret | Infisical agent and `HOMELAB_SECRET_DIR`, [configuration](docs/configuration.md#secrets) |
| Route returns 404/403 through Caddy | [ingress](docs/ingress.md#caddy-routes) |

## How to work here

- Run `scripts/dev/gate` before pushing; it is what CI runs.
- Prefer deleting or reusing code to adding it. If a fix needs a new mechanism, say so and stop for a decision.
- A change to a governed area updates its decision record's links or adds a superseding record; a change to
  behaviour updates the guide page that describes it, in the same PR.
- Tests should exercise behaviour and failure paths. Don't add tests that assert a doc heading, a file name or
  a string's presence ([development](docs/development.md#what-makes-a-useful-test)).
- Never write private values into this repo, even in tests; `scripts/ci/public-readiness.py` will fail.
