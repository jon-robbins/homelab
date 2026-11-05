# Traefik Retirement Remediation Implementation Plan

> **For agentic workers:** Execute this plan task by task using superpowers:executing-plans. Use subagents only if the owner explicitly selects delegation. Steps use checkbox syntax for tracking.

**Goal:** Complete the Caddy retirement by making Traefik ports, routes, local setup, policy records and cutover procedures agree with the deployed Traefik design.

**Architecture:** Keep the official Traefik image and file-provider model. Correct the Compose target ports, make the existing release validation path enforce the route-security contract if the owner approves that new validator, and bring the separate setup wizard onto Traefik. Keep only clearly labelled historical Caddy references and temporary rollback support.

**Tech Stack:** Docker Compose, Traefik v3 file-provider YAML, Python release validation, setup wizard, pytest/nox.

**Spec:** `docs/decisions/0020-traefik-edge.md`, `docs/decisions/0018-local-setup-path.md`, and the owner's 2026-11-05 direction to deprecate Caddy long term.

**Review revision (2026-11-05):** Incorporates the follow-up review of the handover. This document plans implementation and rehearsal; it does not authorise production operations, approve the route validator, waive decision gates or approve a new setup interface.

## Global Constraints

- Preserve one Compose service per `modules/<tier>/<service>.compose.yml`.
- Keep release policy and any new validation in the platform; keep wizard behavior in `setup/`.
- Setup remains within its 2,000-line budget; do not raise budgets in this change.
- Do not raise line or complexity budgets; `C901 <= 10`.
- A published port binds an explicit interface unless listed in `site.yaml` `exposed`; `exposed` names the host-published port.
- Secrets remain files under `${HOMELAB_SECRET_DIR}`.
- Do not add Docker discovery, socket access, Traefik plugins, or a custom Traefik image.
- Do not require zero downtime. Stop the old proxy before applying the replacement and document the outage and recovery path.
- Keep historical Caddy material only when it is clearly marked as history, migration evidence, or a bounded rollback fixture.

## Review Focus

- Published host port and Traefik container target differ; assert both in tests.
- Every site router must clear spoofable identity headers before forward authentication.
- The actual setup phase catalog must match the service catalog and emit Traefik configuration.
- A failed edge apply must have a documented, tested retry or rollback path.
- Historical decision text must not look like current Caddy policy.
- Automatic applies must remain paused throughout cutover and any manual recovery; an already running updater must finish before proxy changes.
- A semantically broken route can pass health checks; preserve the old signed model independently of `edge-good.json`.
- Listener assertions need real traffic probes, including proof that a default router is absent from the tunnel listener.
- Optional services absent from a selected site remain valid; check phase names against the platform catalog.

## Evidence and execution boundaries

- The port-target defect, stale setup phase, unused matrix field and string-only reconcile test are confirmed in this checkout.
- The missing route-policy check is a configuration safety gap. No authentication bypass in the owner's actual routes has been demonstrated; those routes have not been inspected.
- `spike/traefik/FINDINGS.md` and `EVIDENCE.txt`, cited by Decision 0020 at spike commit `bc1f937`, were recovered
  from git history on 2026-11-05. Treat them as historical spike evidence; current site and production acceptance
  require their own results.
- Source changes and local/disposable-host verification precede production approval. Task 6's production checklist requires separate owner operational approval.
- Tasks 1, 3a, 4 and 5 can proceed independently of validator approval. Task 2's parser and Task 3b's new generation interface remain gated; do not report the whole remediation complete while either required deliverable is deferred.

## File Map

- `tests/modules/owner_site/overrides/traefik.yml`, `tests/modules/test_owner_site.py`: fixture listener targets and port-pair contract.
- `scripts/release/compose_policy.py`, `tests/release/test_compose_policy.py` (or a focused existing release test): optional Traefik route contract validation and failure cases.
- `tests/modules/owner_site/edge/traefik.yml`, `tests/modules/site/edge/traefik.yml`, `tests/release/test_render_docker.py`: safe example and synthetic runtime routes.
- `setup/config/phases.json`, `setup/engine/local.py`, `setup/README.md`, `setup/tests/`: Traefik edge phase and generated local-site routes.
- `docs/decisions/0020-traefik-edge.md`, affected earlier decision records, `docs/decisions/README.md`, `docs/ingress.md`, `docs/security.md`, `docs/superpowers/plans/2026-11-05-caddy-retirement.md`: current policy, history and operational guidance.
- `.github/workflows/ci.yml`, `tests/ci/test_workflow_security.py`, `tests/release/test_reconcile.py`, `tests/release/test_workflow.py`: remove dead workflow data and retain behavioral path-filter coverage.
- The owner's private site repository: service selection, Traefik route file, listener override and exposed host ports. It is not present in this checkout.

---

### Task 1: Correct the Traefik listener targets

**Files:**
- Modify: `tests/modules/owner_site/overrides/traefik.yml`
- Test: `tests/modules/test_owner_site.py`
- Modify: `docs/security.md`, `docs/ingress.md` if needed to show the actual host-to-container mapping.

- [ ] Extend the owner-site port assertion to include `target` as well as service, published port, protocol and host IP. Assert `443→4443`, `9443→5443`, `80→8080`, and `8443→8443`.
- [ ] Run the focused test and confirm it fails on the current `80:80` and `8443:443` values.
- [ ] Change only the container targets to `8080` and `8443`; retain the host ports and `site.yaml` `exposed` entries.
- [ ] Correct the security-guide example from `443:443` to `443:4443`; add the explicit LAN mappings to ingress guidance.
- [ ] Run `pytest tests/modules/test_owner_site.py tests/modules/test_catalog.py -q` and confirm the target pairs and host exposure policy pass.
- [ ] Extend `tests/release/test_render_docker.py` with a unique disposable Compose project that retains published ports on numeric loopback addresses and uses available test host ports. Send requests through each published listener to a disposable upstream and assert delivery; cover tailnet TLS, tunnel HTTP, LAN HTTP and LAN TLS. Structural assertions above retain the production host-port contract.
- [ ] Give a synthetic router no `entryPoints`; prove it answers through `tailnet` and returns no upstream response through `tunnel`. Use an explicit tunnel router as a positive control so a dead listener cannot satisfy the negative assertion. Do not mark G2 passed from config assertions or `/healthz` alone.
- [ ] Inspect the owner's private site override and prepare the same target-port correction in its companion site change. Keep private values and runtime evidence outside this public repository.

### Task 2: Approve and enforce the dynamic-route security contract

**Decision gate:** A release-time parser/validator is a new mechanism. Obtain owner approval before implementation. Preferred narrow scope: extend the existing rendered Compose policy path; do not introduce a separate service or general Traefik discovery layer.

**Files:**
- Modify: `scripts/release/compose_policy.py` (or an existing focused policy module if the owner selects it)
- Test: `tests/release/test_compose_policy.py` or a focused existing release test
- Modify: `tests/modules/owner_site/edge/traefik.yml`, `tests/modules/site/edge/traefik.yml`, `tests/release/test_render_docker.py`

- [ ] Add failing behavior tests for a valid route file, a router missing `strip-identity`, `strip-identity` placed after forwardAuth, and a middleware definition that does not clear `Remote-User` and the configured `X-Authentik-*` headers.
- [ ] Include the following bounded syntax contract in the validator approval: accept literal YAML and `{{ env "BASE_DOMAIN" }}` substitutions in scalar values only; require a non-empty service `BASE_DOMAIN`; reject other template expressions with an actionable error rather than evaluating arbitrary Go templates. Inspect the private routes before approval to establish whether this subset is sufficient.
- [ ] Validate the final rendered config mounted at `/etc/traefik/dynamic/routes.yml`, resolving its Compose config source after overrides and inlining. Test an inline site override as well as a file-backed config; neither may bypass policy by replacing the source or config name.
- [ ] For the approved subset, require each HTTP router's first middleware to resolve to the identity-stripping headers definition. Accept bare file-provider names and explicit `@file` references; reject unsupported providers and middleware chains until separately specified. Require router services and middleware references to resolve within the site's dynamic config. Exclude the platform's separate `health.yml` and its `ping@internal` service from this site-router rule. Explicitly reject unsupported routing sections rather than silently leaving them unchecked.
- [ ] Test missing environment values, unsupported templates/providers/chains, unresolved references, renamed config sources and malformed YAML, in addition to the header-contract failures. If the real site requires more syntax, return to the approval proposal before expanding the parser.
- [ ] Update both synthetic site fixtures and `_GOOD_ROUTES` to satisfy the contract.
- [ ] Add an end-to-end failure-path check showing forged `Remote-User`/`X-Authentik-*` headers do not reach an upstream and that gated routes fail closed when Authentik is unavailable. Reuse the existing Docker test session.
- [ ] Add the validator to Decision 0020's governed-code list and document any syntax restrictions in ingress guidance.
- [ ] Run the focused policy/module tests and `nox -s render_docker`.

### Task 3a: Retire Caddy from local setup phase selection

**Files:**
- Modify: `setup/config/phases.json`, `setup/engine/local.py` only if phase handling requires a code change, `setup/README.md`
- Modify: `setup/tests/conftest.py`, `setup/tests/test_local.py`, `setup/tests/test_cli.py`, `setup/tests/test_hostprep.py`

- [ ] Load the real phase configuration and verify each explicit service name exists in the platform module catalog, excluding the `*` sentinel. Render a synthetic selection containing Traefik and prove it is placed in `phase.traefik`, outside the wildcard app phase; cloudflared remains in `phase.tunnel`. A site omitting optional named services must still build valid steps.
- [ ] Change the phase/service key from `caddy` to `traefik`; update setup documentation and phase-related fixtures/assertions.
- [ ] Retain tests that absent optional phase services are ignored and that cloudflared cannot run without GATE-1. Renaming Caddy fixtures must preserve failure/resume and owner-stopped-service coverage.
- [ ] Check the setup line budget and `C901` before completion; cut scope instead of increasing budgets if either is exceeded.
- [ ] Run `nox -s wizard` and `nox -s wizard_e2e` against the synthetic site.

### Task 3b: Specify and implement the separate setup route generator

**Execution decision (2026-11-05): deferred by owner direction.** This is separate wizard development and does not
block finishing the Caddy retirement or preparing the cutover.

**Scope gate:** There is no existing Caddy generator to swap. `setup/cli.py:open_site` consumes a complete existing site read-only. Define and review the generation interface before adding behaviour; phase cleanup in Task 3a does not depend on it.

**Files:** Proposed `setup/routes.py`, `setup/tests/test_routes.py`, plus `setup/README.md`; change `setup/cli.py` only after the invocation is agreed. Do not make `status` or `Release.render` mutate the site.

- [ ] Prepare a concrete interface proposal: route inputs must explicitly name host, selected service, upstream port, optional path prefix and whether authentication is required. Specify which catalog applications have supported route recipes, how URL-base settings are handled, and how family/admin host assignments are supplied. Do not infer upstream ports or expose every selected service automatically.
- [ ] Specify the invocation and output ownership in that proposal: generation is an explicit setup action before rendering, writes only the local site's `edge/traefik.yml`, refuses to overwrite an existing user file, and is deterministic for the same inputs. A rerun may accept identical content without rewriting it; changed existing content requires an explicit user-directed edit. Include the exact callable/CLI signature in the reviewed proposal before implementation.
- [ ] Keep generated routers on the default `tailnet` entry point; generation must not publish host ports or enable tunnel routes. Preserve the preview's loopback-only publication guard: synthetic setup verification uses a loopback override. Real tailnet binding remains deferred to the existing preview security work.
- [ ] Use the identity-stripping definition and order from the shipped example. Gated recipes require explicit Authentik configuration; refuse a gated recipe without its prerequisites. GATE-1/provider integration remains deferred as Decision 0020 records; do not claim generation proves authentication or authorisation.
- [ ] After interface review, implement the generator in `setup/` and test parseable output, supported/unsupported recipes, missing prerequisites, middleware order, no tunnel entry point, existing-file refusal and idempotent reruns.
- [ ] Extend `setup/tests/e2e_docker.py` with a synthetic Traefik selection and generated routes. Its existing example-site tests do not establish Traefik coverage. Verify a request reaches the intended disposable upstream, then rerun apply without recreation; clean up both app and edge resources created by the test.
- [ ] Run `nox -s wizard` and `nox -s wizard_e2e`; check the 2,000-line setup budget and C901. If the agreed interface cannot fit, record the deferred generator explicitly rather than claiming local setup is complete.

### Task 4: Make Caddy retirement records and examples unambiguous

**Files:**
- Modify: `docs/decisions/0020-traefik-edge.md`, `docs/decisions/README.md`
- Update supersession status/notes in: `docs/decisions/0003-tiers.md`, `0008-compose-policy.md`, `0009-image-trust.md`, `0014-dependabot-automerge.md`, `0015-exposure-policy.md`, and relevant sections of `0016-edge-updater.md`
- Modify: `docs/superpowers/plans/2026-11-05-caddy-retirement.md`, `docs/ingress.md`, `docs/security.md`, `setup/README.md`

- [ ] Keep old decision rationale intact; mark Caddy-specific claims superseded by 0020 (or a new superseding record if 0020 does not cover them) rather than silently rewriting history.
- [ ] Record the owner's approval of Traefik as the long-term edge proxy separately from operational acceptance. Create a G1–G10 evidence table with commit/image version, check, result, evidence location and status (proven, partial, open, explicitly deferred or owner-waived). Recover the missing spike evidence before treating its claims as proven.
- [ ] Include G2 runtime listener/default isolation, real G3 RSA/X1 issuance, G4 aliases, G6 real Authentik authorisation, G7 native-Linux source addresses for tailnet/LAN/tunnel/host-local traffic, G9 Dependabot update evidence and G10 swap/recovery. Preserve the existing explicit GATE-1/probe deferrals; do not convert partial gates into passes. Obtain explicit owner approval for any new waiver.
- [ ] Replace current-tense policy examples with Traefik examples; retain dated migration comparisons only when clearly historical.
- [ ] Correct the retirement plan's `REBUILD_PATHS` reference to `BUILD_INPUT_PREFIXES` and mark the plan's completed steps as complete or label the artifact as a historical plan.
- [ ] Review all Caddy matches in operational files; leave only explicitly historical migration/rollback references. Do not remove the edge-swap rollback test while it is needed to cover rollback from the previous signed release.
- [ ] Run documentation link/public-readiness checks.

### Task 5: Remove dead workflow fields and replace the string-only reconcile check

**Files:**
- Modify: `.github/workflows/ci.yml`, `tests/ci/test_workflow_security.py`
- Modify: `tests/release/test_reconcile.py`, `tests/release/test_workflow.py`

- [ ] Add or extend a parity test so each workflow path filter is covered by `BUILD_INPUT_PREFIXES` and each build-input prefix is justified by a workflow filter or documented release input.
- [ ] Remove `test_reconcile_does_not_watch_caddy_directory`; it asserts a literal path string rather than behavior.
- [ ] Remove the unused `cap_drop_check: false` matrix field and its assertion; retain tests for actual cap-drop behavior and policy rejection.
- [ ] Run the focused reconcile, workflow and CI-security tests.

### Task 6: Prepare and execute the bounded-downtime cutover

**Files:**
- Update the private site repository's `site.yaml`, `edge/traefik.yml`, and Traefik override.
- Modify: `docs/operations.md` and/or the cutover runbook named by Decision 0020.
- Verify: `host/homelab-edge-update`, `tests/host/test_edge_update.py`, previous signed edge bundle and GHCR availability.

**Interfaces:** Production consumes the exact verified signed candidate release and a separately preserved previous signed bundle, its rendered edge model, image digests and certificate stores. Disposable rehearsal must prove recovery before operational approval.

- [ ] Prepare matching platform/private-site revisions and render the exact pair together. Document merge order: either intermediate pairing may fail rendering after the old module is removed. Do not treat two merges as atomic; require a signed candidate recording both intended commits before cutover.
- [ ] Extend the existing recovery guidance in `docs/operations.md` rather than duplicating it. Record the current/previous release identifiers, preserve the previous verified signed bundle and edge model outside `state/edge-good.json`, and retain/pull its pinned Caddy image. Confirm both certificate stores survive apply, rollback and cleanup. Preserve the image and model through the acceptance window.
- [ ] Rehearse on a disposable Docker host: stop Caddy, apply Traefik with the updater's flags, force an unhealthy apply and verify automatic rollback, then force a semantically broken route that still passes health and verify manual rollback. Check actual requests and certificate-store preservation, not only container lists. Keep G10 open if this cannot be run.
- [ ] Rehearse recovery when rollback itself fails and when no good model exists. The failed hash remains skipped on later runs; capture explicit operator recovery and verify requests after recovery. A passing subsequent timer run is not evidence that rollback succeeded. Do not rely on deadman failure for edge drift; current operations guidance says it is a notice only.
- [ ] Write the production runbook with exact commands, expected results, stop conditions and rollback commands. Include both the existing `edge-failed` retry procedure and rollback after a healthy-but-wrong apply: success overwrites `edge-good.json`, so use the separately preserved previous model. Keep applies frozen after manual rollback until a corrected signed release is staged; otherwise the timer can immediately reapply the rejected candidate.
- [ ] Obtain the owner's operational approval for the completed runbook before production steps. Before pausing automation, read the homelab context and inspect current host state through the homelab MCP; do not assume the disposable environment matches production.
- [ ] At the approved window, record existing pause/timer state, set `observe-only`, stop the edge timer, and wait for any in-flight edge updater and controller apply to finish. The updater checks `observe-only` only on entry. Stage the exact signed candidate through the supported owner admission path; ordinary reconcile is paused, so the runbook must explicitly describe any intentional operator override. Verify `current` points to the intended candidate before stopping Caddy.
- [ ] With automatic applies still paused, stop Caddy and apply the staged Traefik model using the reviewed manual command with `--no-deps`, orphan removal and health waiting. Since `observe-only` makes the updater exit, do not use its service as the manual apply command while that marker remains set.
- [ ] Verify tailnet TLS, LAN HTTP/TLS, tunnel origin, real Authentik group allow/deny behaviour, forged identity headers, representative upstream paths and actual certificate key/chain. If any acceptance check fails, use the rehearsed rollback while automation remains paused and prove the old routes work again.
- [ ] After all checks pass, reconcile updater bookkeeping through the reviewed procedure and confirm `edge-good.json` represents the accepted model with no stale failed marker for it. Restore the pre-existing pause/timer state only when it is safe; do not clear an owner's earlier pause. Observe a subsequent updater run and verify it does not cause another proxy swap.
- [ ] Record actual G1–G10 results and remaining deferrals/waivers in Decision 0020. Close the rollback window explicitly before deleting retained image/model material or transitional fixtures; retain historical decision evidence.

### Task 7: Final verification

- [ ] Run `scripts/dev/gate --fast` after unit/policy/setup changes.
- [ ] Run `scripts/dev/gate` with Docker available to cover renderer, Traefik and wizard e2e paths.
- [ ] Run `python3 scripts/ci/complexity_budget.py` and `scripts/ci/public-check.sh`.
- [ ] Search tracked files for `caddy` and classify every match as active defect, temporary rollback support, or labelled history; ensure no active setup/build/policy path still selects Caddy.
- [ ] Review the final diff against Decision 0020 and this owner-approved long-term deprecation direction.
- [ ] Report implementation, disposable rehearsal and production acceptance separately. List pending validator/setup-interface approval, unavailable private-site or spike evidence, open gates and any unrun checks. Passing generic wizard/Docker sessions alone must not be presented as evidence for the new listener, route-generation or cutover scenarios.
