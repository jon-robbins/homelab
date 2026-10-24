# 0005: A slim custom controller with file state

- **Status:** Accepted (2026-10-12; budget and cuts 2026-10-17)
- **Code:** `scripts/controller/`, `config/ci/budgets.json`
- **Related:** [0004](0004-signed-full-state-releases.md), [0011](0011-complexity-budgets.md),
  [operations](../operations.md)

## Context

The first design had an admission journal, attempt history, automatic restores, maintenance permissions and a
daemon-lifecycle model. It was never finished. Komodo was evaluated as an off-the-shelf alternative.

## Decision

`homelabctl` is a small Python CLI (`status`, `reconcile`, `rollback`, `ack`) run by a systemd timer every
5 minutes. Its state is plain files in `/srv/homelab/state/` (`highwater`, `applying`, `DEGRADED`, `failures`,
`observe-only`, `lock`), each written as temp file + rename.

Each tick: lock → stop if a previous apply crashed or DEGRADED exists → host checks (disk, markers, Docker) →
fetch the newest release above `highwater` → verify → plan by Compose config hash → snapshot changed stateful
services → `docker compose up -d --no-deps` → wait for healthy → advance pointers. A failure writes DEGRADED
with the exact `homelabctl rollback <svc>` command and alerts. Nothing is restored automatically.

The controller never starts a service the owner stopped, and only touches services listed in `managed`.

## Consequences

- The controller is about 550 formatted lines and fits in one reading.
- After a Docker Compose upgrade every config hash may change, so the next tick recreates every managed
  service (documented in operations).

## Rejected

- Komodo: it would replace only the `compose up` step, while admission, health gating and rollback stay custom;
  it adds a database, a networked Docker agent and deploys that bypass the signed feed.
- Cut on 2026-10-17 to stay inside the budget: `apply --edge`, `homelab.probe` checks, a Compose-version guard,
  and a per-service status table.

## Don't

- Don't add a database, automatic restore or attempt history without a new decision record naming the incident
  that needs it (the deferred "M3b" design).
- Don't raise the line budget to fit a feature; cut the feature ([0011](0011-complexity-budgets.md)).
