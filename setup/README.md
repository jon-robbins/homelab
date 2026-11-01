# Local setup engine

**Status: engine component of an incomplete security/bootstrap preview.** This branch does not provide a finished
guided setup or a working media-server onboarding flow. Do not use it as the installation path for a new host.

This is the engine foundation for the guided local setup in [0018](../docs/decisions/0018-local-setup-path.md).
It has no screens or provider wiring yet. It is separate from the signed owner pipeline and never calls its admission,
materialize or reconcile functions. Nothing in the platform depends on this directory.

Install `setup/requirements.txt` in a Python 3.12 environment. From the platform root, run local setup as root on
Ubuntu 24.04 amd64:

```sh
python3 -m setup.cli --site /path/to/local-site status
python3 -m setup.cli --site /path/to/local-site apply
```

`apply` performs a read-only host preflight and ownership check before installing pinned host packages. Artifact
downloads are verified against `setup/pins.json` before installation. The local Docker unit requires and starts after
Tailscale and the secrets readiness unit, so a missing required secret prevents Docker from starting on reboot.
Preflight refuses a host with a globally addressed interface; there is no acknowledgement override while service
listeners are not yet bound to a validated interface. This check cannot detect router port forwarding to a private
host, so scoped bind validation is still required before preview apply is safe.
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
Both return `Result.success(message, facts)`, `Result.failure(reason)` or, from apply, `Result.ask(prompt)`.
Failures stop all later steps. Unknown dependencies, cycles and duplicate IDs are rejected before applying anything.
Steps never print, read the terminal or launch a browser.

The prompt types are `Text(key, label, default)`, `Secret(key, label, hint)`, `Confirm(key, label)`,
`Choice(key, label, options)` and `OpenUrl(key, label, url)`. The runner's `ask(prompt)` callback returns a string,
boolean or `SecretHandle`; the engine stores secret text in `ctx.secrets` and gives the step only a handle in
`ctx.answers[key]`. Read its value only at the provider boundary with `ctx.secrets.read(handle)`.
Use a Secret prompt for every credential. Never put credentials in messages, facts, ordinary answers or URLs.
The basic CLI is a development adapter; the TUI supplies its own ask callback and Event consumer.

`Context` contains `layout`, `secrets`, `checkpoints`, `platform`, `site`, `plugins`, `answers` and `facts`.
Facts and ordinary answers are JSON data, persisted across runs. Known secret text is redacted from messages and recursively from nested JSON facts;
that is a fallback, not permission for steps to return secrets. Secret handles in ordinary prompt answers or facts fail clearly before persistence. Secret handles are not checkpointed; a resumed step
should find its file with `ctx.secrets.exists(name)` and `ctx.secrets.handle(name)`.

`runner.run(steps, ctx, ask=..., on_event=...)` returns an Outcome with `ok`, `done`, `stopped_at` and `reason`.
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
before rendering or writing, regardless of `--root`. Containers in the reserved Compose projects must point to
this engine's release tree; an unavailable Docker daemon blocks that ownership check. An unknown
ownership marker refuses apply. For a stale marker, first inspect running services and their data, then remove the
marker manually only after establishing that this host is free for local setup. Setup never silently changes modes.

The local network step checks existing host routes and Docker IPAM ranges, selects an unused RFC1918 subnet, and
persists it in `network.json` under the local setup state. Re-runs reuse that plan. The first `/24` is reserved for
the gateway and Caddy address; Docker receives a separate `/24` as its dynamic allocation range.

`build_steps(ctx, release, ops, extra=(), requires={})` builds release, network, directories, secrets and ordered
phases. Core IDs are `engine.release`, `engine.network`, `engine.dirs`, `engine.secrets` and `phase.<name>`.
Use `extra` for provider/wiring/gate steps and `requires={"phase.tunnel": ("GATE-1",)}` to hold a phase until its
external check succeeds. Defaults in `config/phases.json` order Postgres, Authentik server and worker, remaining apps,
Caddy and tunnel. Every phase containing cloudflared requires `GATE-1` by default. Integration wiring supplies that step; `requires` can add dependencies but cannot remove this gate.
Absent it, `runner.run` validates the graph and refuses the run before step effects. The standalone CLI therefore
cannot start a tunnel until integration wiring supplies a forward-auth gate.

Each service starts explicitly with `--no-deps`, after pulling, and waits for health. A container without a healthcheck
must remain running without restarts for ten seconds; this is process readiness, not proof of application wiring.
The owner's `homelab-backup` has fixed paths and credentials, so local setup does not invoke it. Until a local-safe
snapshot is configured, setup refuses to recreate existing stateful data. Stateful edge recreation is refused.
Restore is always manual.
Services explicitly configured stopped are created without starting. Exited containers remain stopped regardless
of checkpoint history, unless setup recorded that it stopped them for a snapshot. That restart intent survives a
killed update and is cleared after health succeeds. Containers that failed to start in the created state may retry.
Running containers with matching configuration are waited for without stopping or snapshotting them again.
Disk markers must already exist, including when the mount point is absent; mount and mark the correct disk first.
New writable bind directories use the service's numeric user/group or PUID/PGID. Existing directories are not chowned.
Dependencies within phases are ordered before dependents; missing or later-phase dependencies are rejected.
Compose output is captured and stdin is closed, so engine steps do not interact with the terminal.

## Verification

`nox -s wizard` covers failure paths, ordering, checkpoint/resume, secret handles and platform independence.
`nox -s wizard_e2e` renders and applies the synthetic example against Docker with guards against existing projects.
Run `scripts/dev/gate` before proposing integration. The setup budget excludes tests and keeps every platform budget
unchanged; ruff formatting and C901 checks include setup code.
