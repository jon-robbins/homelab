# Development

## The gate

Run everything CI runs, from the repository root, before opening a pull request:

```bash
scripts/dev/gate          # pre-commit and every nox session, Docker included (needs Docker running)
scripts/dev/gate --fast   # pre-commit and the no-docker sessions only
```

It runs `pre-commit run --all-files` and `nox`: directly on Linux, and in a pinned Linux container
(`scripts/dev/gate.Dockerfile`) on macOS, so results match CI. On macOS the container drives the host's Docker
daemon through its socket, with the repository and a temporary directory mounted at their host paths so paths
the tests pass to Docker resolve. The Docker sessions are `controller_e2e`, `render_docker` and `images`, which
builds every image in CI's `image-builds` job. Extra arguments go to nox, for example `scripts/dev/gate -s release`.
CI calls the same entry points.

`controller_e2e` creates and removes a Compose project named `homelab`, so outside hosted CI it refuses to run
when that project already exists on the daemon. That keeps it from touching a real host.

## Tests

| Path | Covers | Session |
|---|---|---|
| `tests/release/` | renderer, module rules, Compose policy, bundle, record, signing, feed, release reconciler, workflow wiring | `release` |
| `tests/modules/` | the module catalog renders against a synthetic site | `release` |
| `tests/controller/` | `homelabctl`: planning, reconcile loop, verification, state files | `controller` |
| `tests/integration/controller_e2e.py` | the controller against real Docker | `controller_e2e` |
| `tests/host/` | `install.sh`, `homelab-backup`, `wait-for-secrets`, `move-data.sh` with `DESTDIR` and fakes | — |
| `apps/homelab-workers/src/homelab_workers/tests/` | the worker package | `workers` |
| `tests/boundary/`, `tests/docs/`, `scripts/ci/public-readiness.py` | no private values in tracked files; doc links resolve | `scripts/ci/public-check.sh` |

## What makes a useful test

The suite should catch the bugs that would hurt on the host. Prefer, in order:

1. **Failure paths:** what happens when the feed is unreachable, a signature is wrong, a snapshot fails, a
   container never gets healthy, a disk is missing, the process dies mid-apply.
2. **Behaviour at boundaries:** inputs at and just past a limit, empty and duplicate inputs, ordering
   (newest release wins; older ones are skipped).
3. **Properties over examples** where the input space is large (planning, policy): generated inputs with an
   invariant, not one hand-picked case.

Avoid tests that assert a file name, a doc heading, a log string or that a constant equals itself. They break on
harmless edits and pass when behaviour is wrong. A test that cannot fail for any realistic code change is not
worth keeping.

## Adding a service

1. Add `modules/app/<service>.compose.yml` following the [module rules](modules.md).
2. If it needs secrets, add `config/infisical/templates/<NAME>.tmpl` and a blank `NAME=` line in
   `.env.example`.
3. Run the gate. Then enable it in a site's `site.yaml` (and `managed` when ready).

## Plugins

A private plugin keeps its own package and tests in its own repo and connects through the
[plugin contract](plugins.md). See `examples/plugin-hello/`.

## Rules that CI enforces

Line budgets (`config/ci/budgets.json`) and ruff `C901 <= 10`; SHA-pinned Actions checked by zizmor and
actionlint; hash-pinned release dependencies generated from `config/release/requirements.in` (never hand-edit
`requirements.txt`); public-readiness on every tracked file. See [decisions](decisions/README.md).
