# Documentation

**Setting up and running a homelab**

1. [Architecture](architecture/overview.md): how the pieces fit together.
2. [Accounts and external services](accounts.md): what to sign up for and why.
3. [Getting started](getting-started.md): try it off-host, then set up a real host.
4. [Configuration](configuration.md): the site repo, `site.yaml`, `site.env`, secrets.
5. [Ingress](ingress.md): LAN, Tailscale, Cloudflare Tunnel, port forwards, Caddy routes.
6. [Security](security.md): threat model, exposure policy, checklist.
7. [Operations](operations.md): day-to-day commands, updates, backups, alerts.
8. [Troubleshooting](troubleshooting.md): by symptom.

**Extending it**

- [Modules](modules.md): the five rules for a service file.
- [Plugins](plugins.md): your own services, with or without code.
- [homelab-mcp](mcp.md): agent access to files and the media apps, and its extensions.
- [Development](development.md): the gate, the test suites, what makes a useful test.
- [Releases](releases.md): the release record and feed in detail.

**Why it is the way it is**

- [Decision records](decisions/README.md)
- [Notes for coding agents](../AGENTS.md)
