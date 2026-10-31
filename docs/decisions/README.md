# Decision records

Each file records one architectural decision: what was decided, why, what it costs, and what was rejected.
They exist so that a person or an LLM changing the code can tell a deliberate constraint from an accident, and
does not re-open a settled question without new evidence.

Rules:

- A record is never edited to change its meaning. A new decision gets a new record that says
  `Supersedes: NNNN`, and the old record's status becomes `Superseded by NNNN`.
- Every record names the code it governs under **Code**. If you change that code in a way the record forbids,
  write a new record first.
- **Don't** lists the changes that look like improvements but were considered and rejected.

| # | Decision | Status |
|---|---|---|
| [0001](0001-compose-modules.md) | Services are plain Compose files with `homelab.*` labels | Accepted |
| [0002](0002-public-platform-private-site.md) | Public platform, private site repo, plugin repos | Accepted |
| [0003](0003-tiers.md) | Host, edge, app and unmanaged tiers | Accepted |
| [0004](0004-signed-full-state-releases.md) | Signed, full-state releases on an event-driven trigger | Accepted |
| [0005](0005-slim-controller.md) | A slim custom controller with file state | Accepted |
| [0006](0006-host-layout.md) | Everything the platform owns lives under `/srv/homelab` | Accepted |
| [0007](0007-disk-markers.md) | Disk markers instead of mount ordering | Accepted |
| [0008](0008-compose-policy.md) | Compose policy v2 and inlined config files | Accepted |
| [0009](0009-image-trust.md) | Digest pins, allowlisted namespaces, attested first-party images | Accepted |
| [0010](0010-secrets.md) | Secrets are files rendered by the Infisical agent | Accepted |
| [0011](0011-complexity-budgets.md) | Complexity budgets with a kill switch | Accepted |
| [0012](0012-backups.md) | Nightly restic plus off-site copy; snapshot before change | Accepted |
| [0013](0013-deadman-alerting.md) | Deadman alerting through healthchecks.io | Accepted |
| [0014](0014-dependabot-automerge.md) | App-tier patch and minor updates merge and deploy unattended | Accepted |
| [0015](0015-exposure-policy.md) | Nothing is reachable from the internet unless allowlisted | Proposed |
| [0016](0016-edge-updater.md) | The edge tier updates itself, with rollback | Accepted |
| [0017](0017-personal-services-are-plugins.md) | Personal services are plugins, not catalog modules | Accepted |
| [0018](0018-local-setup-path.md) | Guided local setup for new homelabs | Accepted |
