# 0013: Deadman alerting through healthchecks.io

- **Status:** Accepted (2026-10-15)
- **Code:** `scripts/controller/homelabctl.py` (`HttpPinger`), `scripts/controller/reconcile.py`, `host/homelab-backup`
- **Related:** [0012](0012-backups.md), [operations](../operations.md#alerts)

## Decision

Alerting is a deadman switch, not a monitoring stack. One healthchecks.io account, two checks:

- **controller:** pinged every tick (also in observe-only mode), 20-minute grace. A tick that finds DEGRADED, a
  disk under 10% free, a missing disk marker, Docker unreachable or a container start error sends a *fail* ping
  naming the cause.
- **backup:** pinged after each successful nightly run; fail ping on error.

healthchecks.io notifies through its own integrations (Telegram, email). The ping URLs live in
`/srv/homelab/trust/deadman.env` (`CONTROLLER_PING_URL`, `BACKUP_PING_URL`), so moving to a self-hosted
Healthchecks is a two-URL swap.

## Consequences

- If the host is down or offline, the missing ping is the alert.
- There is no alert on a service being merely slow or on unusual CPU use.

## Don't

- Don't add a metrics stack to get alerts; add a cause to the controller's step-3 checks instead.
