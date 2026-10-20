# 0012: Nightly restic plus off-site copy; snapshot before change

- **Status:** Accepted (2026-09-27)
- **Code:** `host/homelab-backup`, `host/install.sh` (backup timer), `scripts/controller/reconcile.py` (step 7)
- **Related:** [0006](0006-host-layout.md), [0013](0013-deadman-alerting.md), [operations](../operations.md#backups)

## Decision

- `homelab-backup` runs nightly (04:15, `homelab-backup.timer`). SQLite databases are copied with
  `sqlite3 .backup` into a staging folder, so the snapshot is consistent while services run; the rest of the data
  tree is backed up directly.
- An optional off-site restic repository (for example Google Drive through rclone, `drive.file` scope, separate
  password) receives a `restic copy` after each nightly run.
- Retention is 30 days on both. Each run ends with `restic check --read-data-subset=2%`.
- Before the controller recreates a stateful service it stops it and takes a tagged per-service snapshot
  (`--service S --tag seq-N`). `homelabctl rollback` restores from that snapshot. Nothing restores automatically.

## Consequences

- The host holds the off-site credentials, so a host compromise could delete the off-site copy. Mitigated by
  encryption, a narrow token scope and the provider's trash; accepted.
- A restore drill was done once for the SQLite class of services; repeat it after changing the backup script.

## Don't

- Don't back up a live SQLite file with a plain file copy.
- Don't make rollback restore data without the owner running the command.
