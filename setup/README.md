# Local setup engine

**Status: engine component of an incomplete security/bootstrap preview.** This branch does not provide a finished
guided setup or a working media-server onboarding flow. Do not use it as the installation path for a new host.

This is the engine foundation for the guided local setup in [0018](../docs/decisions/0018-local-setup-path.md).
It has no screens or provider wiring yet. It is separate from the signed owner pipeline and never calls its admission,
materialize or reconcile functions. Nothing in the platform depends on this directory.

Setup needs Python 3.12 and PyYAML (Ubuntu's `python3-yaml`; `bootstrap.sh` installs it). From the platform root,
run local setup as root on Ubuntu 24.04 amd64:

```sh
python3 -m setup.cli prepare
python3 -m setup.cli --site /path/to/local-site apply
python3 -m setup.cli --site /path/to/local-site status
```

`prepare` (also the first part of `apply`) runs a read-only host preflight and ownership check, then installs Docker
from Docker's apt repository (key pinned by digest) and sets Docker log limits.
Preflight refuses a host with a public IPv4 address. A public IPv6 address only prints a note: home ISPs give every
host one, and the router's firewall keeps it private. Published ports bind loopback only (below), so IPv6 can expose
host services such as sshd but not the media stack. This check cannot detect router port forwarding to a private
host. As an additional incomplete-preview guard, apply rejects published ports in both rendered Compose models unless
each has an explicit numeric `127.0.0.1` or `::1` host bind. This intentionally blocks wildcard, missing and private
LAN binds; router forwarding makes private LAN binds unsafe. Tailscale binds are not allowed in this preview guard.
Scoped bind validation and verified tailnet binding are required before this restriction can be relaxed.
Review the generated site before continuing. The renderer enforces the platform Compose policy and image pins. Local
apply does not authenticate a downloaded
platform version: signed release verification belongs to the WS-13 release/update contract. This engine is not a
replacement for signed admission on owner hosts.

## Step contract

Import these types from `setup.engine.contract`:

```python
class Step(Protocol):
    id: str
    needs: tuple[str, ...]

    def check(self, ctx: Context) -> State: ...
    def apply(self, ctx: Context) -> Result: ...
    def verify(self, ctx: Context) -> Result: ...
```

`check` is read-only and returns `State.done()`, `State.needed()` or `State.blocked(reason)`. Check the external
system every time; checkpoint history never substitutes for current truth. `apply` must be safe to retry after any
partial write. `verify` independently proves success and may return facts recovered from the external system.
A step whose `check` already observes the host can inherit `CheckIsProof`, whose `verify` is `check` returning DONE.
Both return `Result.success(message, facts)` or `Result.failure(reason)`.
Failures stop all later steps. Unknown dependencies, cycles and duplicate IDs are rejected before applying anything.
Steps never print, read the terminal or launch a browser.

`Context` contains `layout`, `secrets`, `checkpoints`, `platform`, `site` and `facts`. Facts are JSON data,
persisted across runs. Known secret text is redacted from messages and recursively from nested JSON facts; that is a
fallback, not permission for steps to return secrets. Steps never ask the person anything: there are no prompts.

`runner.run(steps, ctx, on_event=...)` returns an Outcome with `ok`, `done`, `stopped_at` and `reason`.
It checks and verifies even previously completed steps. SIGTERM, SIGHUP and keyboard interruption leave the
started checkpoint; SIGKILL leaves the same history. Run apply again to resume. A host lock refuses concurrent runs.
`runner.status(steps, ctx)` calls only check and does not mutate checkpoint history.

## Local apply

Local files live under `/srv/homelab/setup/`: `checkpoints.json`, `setup.lock` and content-addressed
`releases/`. This state is independent of signed feed sequence numbers. `--root` changes the engine state location;
it does not relocate the host backup utility's data tree. A `local-setup.owner` marker is written at both the
chosen root and `/srv/homelab`, so the signed installer refuses even an alternate-root local installation. Secret files use the site's `HOMELAB_SECRET_DIR` and
are written atomically in a 0700 directory. Generated secrets survive reruns; provider-issued secrets are never invented.
Setup records generated secret names and stops if one disappears. If checkpoints are lost, it also checks Docker
and persistent data before generating a secret. Docker inspection errors stop this check.

Owner trust, tooling (including a partial installation), current release or controller units refuse local commands
before rendering or writing, regardless of `--root`. The reserved Compose projects (`homelab`, `homelab-edge`, from
`docker compose ls`) must point to this engine's release tree; an unavailable Docker daemon blocks that check. An unknown
ownership marker refuses apply. For a stale marker, first inspect running services and their data, then remove the
marker manually only after establishing that this host is free for local setup. Setup never silently changes modes.

The network step creates `homelab_net` with `docker network create --label homelab.setup=local`. Docker picks
a subnet from its own address pools that no host route or other network uses; to steer that choice, set
`default-address-pools` in `/etc/docker/daemon.json`. An existing `homelab_net` without the label is refused.

`build_steps(ctx, release, ops, extra=(), requires={})` builds release, network, directories, secrets and ordered
phases. Core IDs are `engine.release`, `engine.network`, `engine.dirs`, `engine.secrets` and `phase.<name>`.
Use `extra` for provider/wiring/gate steps and `requires={"phase.tunnel": ("GATE-1",)}` to hold a phase until its
external check succeeds. Defaults in `config/phases.json` order Postgres, Authentik server and worker, remaining apps,
Traefik and tunnel. Every phase containing cloudflared requires `GATE-1` by default. Integration wiring supplies that step; `requires` can add dependencies but cannot remove this gate.
Absent it, `runner.run` validates the graph and refuses the run before step effects. The standalone CLI therefore
cannot start a tunnel until integration wiring supplies a forward-auth gate.

Each service starts explicitly with `--no-deps`, after pulling, through `docker compose up --wait`, which returns once
the container is healthy (or running, when it has no healthcheck). `check` and `verify` read health without waiting
and treat a restarted container as failing. Setup takes no snapshots and refuses to recreate a stateful service over
existing data (a container or a non-empty data directory); back it up first. Restore is always manual.
Services explicitly configured stopped are created without starting. Exited containers remain stopped regardless
of checkpoint history. Containers that failed to start in the created state may retry. A running container with
matching configuration that is still starting is waited for with `up --wait`, which recreates nothing.
Disk markers must already exist, including when the mount point is absent; mount and mark the correct disk first.
New writable bind directories use the service's numeric user/group or PUID/PGID. Existing directories are not chowned.
Dependencies within phases are ordered before dependents; missing or later-phase dependencies are rejected.
Compose output is captured and stdin is closed, so engine steps do not interact with the terminal.

## Verification

`nox -s wizard` covers failure paths, ordering, checkpoint/resume, secret handles and platform independence.
`nox -s wizard_e2e` renders and applies the synthetic example against Docker with guards against existing projects.
Run `scripts/dev/gate` before proposing integration. The setup budget excludes tests and keeps every platform budget
unchanged; ruff formatting and C901 checks include setup code.
