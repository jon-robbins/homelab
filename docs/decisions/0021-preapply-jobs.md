# 0021: Pre-apply jobs through Compose `run`

- **Status:** Accepted (2026-11-24)
- **Code:** `scripts/controller/reconcile.py`, `scripts/release/compose_policy.py`
- **Related:** [0004](0004-signed-full-state-releases.md), [0005](0005-slim-controller.md),
  [0008](0008-compose-policy.md), [operations](../operations.md#pre-apply-jobs)

## Context

Some release steps must run after new images are available and before any managed container is recreated or
started. Database migrations are the first use case: the schema must advance while the previous release's services
are still running.

## Decision

A stateless managed service may carry `homelab.preapply: <mode>`, where `<mode>` is one shell token passed as the
command to `docker compose run`. When that service is in `recreate` or `create_stopped`, the controller, after
`docker compose pull` and before any stateful stop or `compose up`, runs:

`docker compose -p homelab -f app.json run --rm --no-deps -T <svc> <mode>`

A non-zero exit uses the pre-apply failure path: no container changes, `highwater` stays, `failures` increments,
and the deadman ping fails on the third consecutive failure. No `DEGRADED` file is written. A newer signed release
above `highwater` replaces a blocked one on the next tick. When the config hash is unchanged the service is not
planned, so the job does not run.

## Consequences

- Migrations and similar work ship in digest-pinned images and ride the normal signed release pipeline.
- A failed pre-apply job blocks the whole release until it succeeds or a newer release supersedes it.

## Rejected

- Running pre-apply after `compose up`: would apply schema changes while old containers still serve traffic without
  a coordinated cutover.
- A separate host script or checkout path: bypasses signed releases and the slim controller model ([0005](0005-slim-controller.md)).

## Don't

- Don't put `homelab.preapply` on a stateful service; snapshots and rollback assume state changes only after the
  job succeeds.
- Don't use more than one token in the label value; the controller passes it as a single `run` argument.