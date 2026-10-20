# 0010: Secrets are files rendered by the Infisical agent

- **Status:** Accepted (2026-10-11; reload hook retired 2026-10-15)
- **Code:** `config/infisical/`, `config/systemd/homelab-infisical-agent.service`, `scripts/release/compose_policy.py`
- **Related:** [0008](0008-compose-policy.md), [configuration](../configuration.md#secrets), [security](../security.md)

## Decision

- Values live in Infisical. The Infisical agent on the host renders one file per secret into
  `HOMELAB_SECRET_DIR`.
- Modules declare which secret names a service gets (`secrets:` with `NAME: {file: ${HOMELAB_SECRET_DIR}/NAME}`).
  Names are public; values never enter a release, a log or the repo.
- After a secret rotates, the owner force-recreates the services that use it (one documented command). There is
  no reload hook and no secret-activation journal.

## Consequences

- Anyone who controls the host can read every runtime secret. That is accepted; the defence is keeping the host
  unreachable ([0015](0015-exposure-policy.md)).
- `CLOUDFLARE_TOKEN` is the most powerful secret on the host: scope it to DNS edits on one zone.

## Don't

- Don't put a secret in `environment:`, `site.env`, `.env.example`, a Compose `configs` entry or a docs example.
- Don't reintroduce an automatic reload hook; it caused restarts nobody asked for.
