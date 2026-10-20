# 0006: Everything the platform owns lives under `/srv/homelab`

- **Status:** Accepted (2026-10-15; supersedes the 2026-09-27 `/srv/appdata` plan)
- **Code:** `host/install.sh`, `host/move-data.sh`, `host/homelab-backup`, `scripts/controller/homelabctl.py`
- **Related:** [0008](0008-compose-policy.md), [0012](0012-backups.md)

## Decision

| Path | Holds | Backed up |
|---|---|---|
| `/srv/homelab/trust/` (root `0700`) | `policy.json`, `feed.token`, `docker/config.json`, `deadman.env` | yes |
| `/srv/homelab/tooling/<sha>`, `tooling/current` | installed controller versions (previous kept) | no, reinstallable |
| `/srv/homelab/state/` | controller state files | yes |
| `/srv/homelab/releases/<seq>`, `current`, `previous` | unpacked, read-only releases | no, re-fetchable |
| `/srv/homelab/data/<service>` | app data | nightly and before each change |
| `/srv/homelab/retired/<service>` | data of retired services | final snapshot only |

Files the OS reads from fixed places (systemd units, the Docker drop-in, `/etc/sysctl.d/90-homelab.conf`,
`/var/lib/tailscale`) stay there and are listed in `/srv/homelab/HOST-FILES`.

## Consequences

- Backups, permissions and "what does this project own?" have one answer.
- Releases and apps never write to `tooling/` or `trust/`; the Compose policy rejects binds that cover
  `/srv/homelab` except `data/`.
- Data was moved in per service with `cp -a` (never `mv`), so the old tree stayed complete for rollback.

## Don't

- Don't install files outside this tree without adding them to `HOST-FILES`.
- Don't point a container bind at `trust/`, `state/`, `releases/` or `tooling/`.
