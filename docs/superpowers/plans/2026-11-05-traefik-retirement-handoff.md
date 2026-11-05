# Traefik Retirement Handover

**Updated:** 2026-11-05
**Worktree:** `cleanup/retire-caddy` in the Traefik migration workspace
**Detailed plan:** [`2026-11-05-traefik-retirement-remediation.md`](2026-11-05-traefik-retirement-remediation.md)

## Latest status: isolated VM verification

The owner approved using a VM after the incident below. This section supersedes earlier gate and rehearsal
status and records verification before PR preparation. Publishing the review branch does not authorise a merge
or a production deployment.

- **Full gate passed in an independent native Linux VM**, exit zero. It tested HEAD `188d0860` plus the transferred
  working changes, verified against diff SHA-256
  `17809c293aa4` (prefix). Pre-commit and budgets passed; release 242, controller 58, controller end-to-end 6,
  Traefik Docker 5, docs 70, host 40 (1 skipped), wizard 132 (1 skipped), wizard end-to-end 4 passed. The image
  build succeeded with the VM's BuildKit. The first attempt lacked `HOME` and `GOCACHE`; the successful run set
  those guest environment variables. Retained local log: `/tmp/traefik-final-vm/gate-full-r2.log`.
- **Real recovery rehearsal passed in that VM**, using the shipped updater, pinned old and new proxy images,
  synthetic configuration and an initially empty guest daemon. It checks the guest hostname and expected Docker
  daemon ID before creating resources. There is no host Docker socket or project-name wrapper involved.
- Proven scenarios: Caddy-to-Traefik swap; observe-only refusal to apply; forced-unhealthy rollback to Caddy;
  retention and subsequent skip of the rejected hash; successful different candidate clearing that marker; and
  manual recovery from a healthy Traefik whose route incorrectly became reachable on another listener. The last
  recovery uses a separately retained previous model after `edge-good.json` has been overwritten.
- Both proxies use locally generated, self-signed RSA certificate/key fixtures. Verified HTTPS requests return
  200 before the swap, after the swap and after both recoveries; hashes of both certificate/key pairs remain
  unchanged. These are functional test certificates, not production ACME stores or evidence of real DNS issuance.
  Retained local log: `/tmp/traefik-final-vm/rehearsal-recovery-r2.log`; final marker `REHEARSAL_EXIT=0`.
- The first isolated rehearsal edited inline routes without refreshing `homelab.config-digest`; Compose correctly
  did not recreate the service. The corrected run calls the existing renderer's `_add_config_digests` for every
  fixture before applying it. The first attempt is not accepted recovery evidence.
- Test containers, synthetic certificates, models and logs remain inside the VM for inspection. Earlier host
  incident artifacts are separate and have not been cleaned up. Do not rerun guarded end-to-end sessions until
  the VM's retained test resources have been accounted for. Production was not changed during VM verification.
- Decision 0020 remains Proposed: real DNS issuance/chain, Authentik allow/deny, live header stripping, actual-site
  acceptance and other listed gates are not established by these results. The route validator and setup route
  generator remain separately gated or deferred. Exact committed platform/site pairing and a signed candidate
  are still required before any further production deployment.
- The private cutover script now has a saved-model completion record checked by preflight, apply, rollback and
  close. Failed image retention during a re-save invalidates completion without deleting the owner's first pause
  record. The coordinator independently reran its 31 focused tests, shell syntax, ShellCheck and diff checks:
  all passed. The worker demonstrated three failures before the final completion-record fixes. Independent final
  review completed with no required integration changes; these private working-tree changes remain uncommitted.
- The reviewer checked the final public diff, VM gate and recovery evidence, private save/re-save and close guards,
  and bounded acceptance claims. Optional operator safeguards remain: refusing a manually re-applied failed digest
  and a clearer old-proxy stop-failure message. Neither was required by the reviewed design; neither was added.

## Earlier production incident and restoration

These earlier host results are retained as incident history, not current VM verification status.

- A rehearsal agent used a nonexistent macOS-style directory in `PATH` on Linux. Its project-name wrapper was
  bypassed, so the real edge updater applied a synthetic model to the production `homelab-edge` project. It replaced
  Traefik, removed cloudflared and created a scratch upstream. Scratch controller state did not isolate Docker.
- The owner explicitly approved restoration from the real current release. At 2026-11-05T18:00:30Z the agent ran
  `docker compose -p homelab-edge -f /srv/homelab/releases/current/edge.json up -d --no-deps --wait --wait-timeout 180 --remove-orphans`;
  it exited zero. The coordinator independently confirmed only Traefik and cloudflared remain in that project,
  both healthy, with the real release labels and the previous Traefik config hash restored. Cloudflared logs show
  a registered tunnel connection. Two verified-TLS probes returned 404 and 302; the 404 is inconclusive for app
  behaviour, and neither probe establishes full application or authorization acceptance.
- The agent confirmed the original ACME file is present with mode `0600` and the previous modification time.
  No certificate contents were dumped. Original stores were not deleted. Scratch resources and incident logs remain
  under `/tmp`; do not remove them without checking live references. Repair log: `/tmp/retire-repair.log`.
- **Earlier host full gate failed**, according to `/tmp/retire-gate-full.log`: release 242, controller 58, native
  `render_docker` 5, docs 70, host 41 and wizard 133 tests passed; pre-commit and budgets passed. Controller and
  wizard end-to-end sessions refused the production daemon as designed. The image build failed because BuildKit
  support was unavailable. Do not weaken those guards or change production tooling to force a test run.
- **The host attempt does not establish G10.** The attempted swap/rollback rehearsal is invalid evidence because it touched production;
  synthetic certificate-store markers also do not prove preservation of real keys and chains. Further recovery
  rehearsal and guarded end-to-end tests require a separate disposable Docker daemon, not a project-name wrapper
  on the production daemon. The owner subsequently authorised the isolated VM work recorded above; host rehearsal
  and host cleanup have not resumed.
- The private-script agent subsequently repaired its test harness and added regressions for verification reruns,
  incomplete initial saves and busy automation at close. That earlier pass reported 28 focused tests passed after demonstrating
  three failures before the script fixes, plus clean shell syntax, ShellCheck and diff checks. These are local
  working-tree changes; they do not reproduce or establish the earlier other-host 30-test result, nor production
  acceptance. The subsequent 31-test completion-record pass and final review are recorded above. Exact signed
  release pairing remains necessary.

## Goal and owner direction

Finish retiring Caddy as an active proxy and make the platform, setup wizard, tests and operating guidance agree on Traefik. The owner has chosen Traefik as the long-term proxy; downtime during cutover is acceptable. Caddy remains only as dated history or bounded rollback material.

The owner asked to pause implementation while reviewing the handover, then authorised implementation with Qwen 3.8 Flash Next agents, up to five at a time. Do not treat that authorisation as approval for the separately gated route validator or production operations.

## Current worktree state

The public review batch contains the implementation, documentation and planning records described below.
The PR identifies its final commit; the private site's working-tree changes remain separate.

### Completed implementation (owner's macOS host: prior-chat evidence, not rerun here)

- **Listener targets:** `tests/modules/owner_site/overrides/traefik.yml` now maps host ports `443→4443`, `9443→5443`, `80→8080`, and `8443→8443`. The contract test checks both published and target ports. Security and ingress guidance show the corrected mappings. Existing host ports and `site.yaml` exposure declarations were left unchanged.
- **Listener probes:** `tests/release/test_render_docker.py` now exercises tailnet TLS, tunnel HTTP, LAN HTTP/TLS, and default-router isolation using loopback-published ephemeral ports and a disposable upstream. Qwen reported the focused test passed and the full Docker test file passed. The primary shell could not connect to Docker Desktop, so those results could not be rerun here.
- **Workflow cleanup:** removed the unused `cap_drop_check` matrix field and its obsolete `noxfile.py` conditional, plus the literal Caddy path test. Added parity coverage between workflow filters and `BUILD_INPUT_PREFIXES`.
- **Setup phase migration:** phase configuration and fixtures now use Traefik. `pytest setup/tests/test_local.py setup/tests/test_cli.py setup/tests/test_hostprep.py -q` — 72 passed; full wizard session — 132 passed, 1 skipped. Tests cover platform catalogue names, Traefik outside wildcard app phase, optional service omission, and GATE-1 for cloudflared.
- **Decision/docs edits:** historical Caddy rationale is labelled, Decision 0020 keeps open/unavailable gates explicit, and ingress/security show the corrected ports. `pytest tests/docs -q` — 70 passed; public-readiness and complexity checks passed.
- **Fast gate:** `scripts/dev/gate --fast` passed: release 242, controller 58, docs 70, host 39 passed/2 skipped, wizard 132 passed/1 skipped; lint and policy hooks passed.

### Fresh no-Docker rerun (2026-11-05, public-integration agent)

Attribution: prior-chat numbers above are from the owner's macOS shell; the block below is a fresh Linux rerun in a
throwaway uv venv (Python 3.12.3, pytest 9.2.0, PyYAML 6.0.3, nox 2025.2.9) because the copied `.nox` environments
carry macOS interpreter links and system `python3` here has no pytest.

- `tests/release tests/modules tests/images` excluding `test_render_docker.py`: 242 passed. `tests/controller` 58.
  `tests/docs` 70. `tests/host` 41 (the two earlier skips ran here; the suite uses a fake `docker`, no daemon).
  `tests/boundary tests/ci` 36 passed / 3 skipped (private-marker checks need `HOMELAB_PRIVATE_MARKERS_FILE`).
  `setup/tests` plus `tests/boundary/test_setup_boundary.py` 133 passed. `scripts/ci/complexity_budget.py` and
  `scripts/ci/public-readiness.py` exit 0. Read-only pre-commit hooks pass: check-yaml, actionlint, gitleaks, shellcheck,
  `bash -n`, complexity budget, public readiness, workflow security; `ruff --select C901` on the governed files passes.
- **Environment artefacts, not repo defects:** `scripts/ci/public-check.sh` cannot run its pytest steps on a bare Linux
  host (`python3 -m pytest` with no pytest on system python), so the "documentation checks passed" claim above is not
  reproducible there; run it from the nox/gate environment. The setup-boundary and workflow-command tests need
  `python` on `PATH` and `PyYAML` importable from `sys.executable`, which the nox sessions provide.
- **Docker suites untouched by this pass:** a Docker daemon is reachable on this box, so `render_docker`,
  `controller_e2e`, `wizard_e2e` and `images` were deliberately not run; a sibling owns Docker work. `ruff-format` and
  `scripts/dev/gate --fast` were not re-run here because formatters can rewrite files during review. The isolated
  native-Linux VM run at the top of this file supplies the Docker-backed gate and the recovery rehearsal, so it
  supersedes this pass's no-Docker coverage.

### Explicitly not implemented

- The release-time Traefik route validator remains unimplemented pending the owner's explicit approval of the new mechanism. Current evidence establishes a configuration safety gap, not a demonstrated bypass in the unavailable private site's actual routes.
- The setup route generator is deferred by owner direction after review. There is no existing Caddy generator;
  setup currently consumes a complete site read-only. It is separate wizard development, not a cutover prerequisite.
- No private site changes, disposable-host rehearsal or production cutover were performed.

### Private cutover script: prior chat versus the local checkout

Two different states are being conflated by earlier notes; keep them apart.

- **Prior lead chat (12a8ab3f, owner's other machine):** reported the private `scripts/traefik-cutover.sh` at
  **30 mock tests passing** after three review rounds (stale-verification, incomplete-save and busy-at-close guards).
- **Local private checkout (reported by the private-site worker on this machine, read-only):** branch
  `feat/traefik-edge` with the script rewritten, `README.md` cutover section, and **untracked** `tests/test_cutover.py`
  holding **25** test functions — 19 passed / 6 failed as the file stands. No ref anywhere in that checkout,
  including locally cached `origin/*`, has ever contained `tests/test_cutover.py`, so the 30-test version is not on
  this machine. All six failures trace to four harness defects in the test file (a `docker` shim that greps
  `docker inspect`/`docker pull` against `$*`, so both branches are dead; a `verify_ok` helper that returns `None`;
  a `save_ok` helper with no "pause already in effect" variant; a shared `systemctl is-active` call counter that
  earlier stages exhaust), not to the script. In a scratch copy under `/tmp` with those four harness fixes: 25 passed /
  0 failed. `bash -n` and `shellcheck -S warning` on the script are clean.
- **Consequence:** do not describe this repo's or this machine's state as "30/30" or as "close guards fixed". The local
  script's `close` had **no** `require_quiet` re-check (apply and rollback do), and no busy-at-close test existed in the
  25. That guard is part of the already reviewed design; it was sequenced below, not claimed here.
- **Fix pass on the private side (reported by the private-site worker later the same day; working tree only, nothing
  staged or committed; not re-run by the public-integration agent):** the four harness defects were repaired first,
  with the assertion count held at 25 tests / 67 asserts before and after the repair, so nothing was weakened.
  Repaired harness against the **unchanged** script: 25 passed / 0 failed. Three new behaviour tests were then written
  against local code and shown red first (3 failed / 25 passed): a failed `verify` rerun could leave an earlier
  verified record that `close` would honour; `apply` could run after an incomplete save when an owner's pre-existing
  pause happened to satisfy the pause check; `close` mutated good/pause/timer state without re-proving units were
  quiet. Restoring only safeguards the script already used elsewhere (the preflight quiet expression, the
  void-before-mutating pattern, the `require_quiet` helper) turned them green: **28 passed / 0 failed**, guards now at
  `apply`, `verify` and `close` entry. `bash -n` clean, `shellcheck -S warning` clean, `git diff --check` clean.
  Site tests unaffected: non-Docker files 44 passed / 2 skipped; the Docker-backed edge file (122 tests collected)
  belongs to the Docker sibling and was not run. Dependabot automerge pause stays operator-managed and documented —
  no new workflow-state mechanism was added.
- **Retry hole found and closed on the private side (same worker, later still; working tree only, not re-run here):**
  with the owner already paused and units quiet, a re-save whose image retention fails left the saved model plus the
  owner's pause record on disk, and `preflight`/`apply` both proceeded (rc=0). Reproduced as 3 red tests first
  (3 failed / 28 passed), then closed with the already-reviewed safeguard: a hash-bound save-completion record
  (same `edge_sha` helper and `die`/FAIL conventions), written only after all pinned-image pulls and the pause
  recording succeed, invalidated at the start of every save attempt, and checked through one helper at the entry of
  `preflight`, `apply`, `rollback` and `close` — so a half-save blocks all four stages, including the rollback stage
  that needs the retained images. `pause-state` is never deleted, so the owner's original pause/timer record survives
  a failed re-save; the completion record, not file existence, decides saved-state safety. Suite grew 28 → **31 tests
  passed / 0 failed**, 81 → 89 asserts, none removed or relaxed; new tests cover failed-re-save blocking
  preflight/apply, a foreign model refused on hash mismatch, and the record having to name the saved model's hash
  rather than being a bare marker. `bash -n`, `shellcheck -S warning` and `git diff --check` clean; site tests still
  44 passed / 2 skipped. Necessity of the addition checked by grep: the completion helper has exactly four call
  sites (the four stages) and the new record is read only through it.
- **Local script versus the public runbook (`docs/operations.md` §Proxy cutover):** `apply` derives the dropped proxy
  by diffing the saved model against current and refuses when nothing is dropped, so an already-swapped model cannot
  be re-applied; no stage materialises the candidate (manual by design, matching runbook step 3); the Dependabot
  automerge pause/restore in runbook steps 1 and 7 is operator-side and documented as such in the script's README.
- **Pairing (locally cached refs, no fetch run):** the site's `origin/main` still selects the old proxy and has no
  Traefik route file, while the platform cleanup branch has already removed the old proxy module from `modules/edge`
  and platform `main` still ships it. On those cached refs the *intermediate* migration pair (platform half merged,
  site half not) cannot render; both halves must land as one signed candidate recording both commits, and the
  `<approved-sha>` the script's `apply` needs must come from that signed candidate, not from a dirty-tree render.
- **Ref-vs-deployment inconsistency to resolve before pairing:** these cached refs suggest the old proxy is what a
  release would currently render, but the incident record above shows the deployed edge actually running Traefik and
  cloudflared with the real release labels. So the cached refs are stale relative to the other machine's site `main`,
  not a production defect either way. Re-fetch and compare before choosing commits; do not pair from cached refs.

## Public-integration review findings (2026-11-05, this worktree)

Behaviour and policy were reviewed against the committed retirement (`e5480eb`, `63f38d7`) plus the uncommitted
diff with no source edits; a later bounded docs pass then fixed items 4 and 5 in place (marked as such).

1. **Exposure policy and docs agree.** `scripts/release/compose_policy.py` keys `site.yaml exposed` on the
   **published host port** (`f"{name}:{port['published']}/{protocol}"`), so moving the container target to `8080`/`8443`
   keeps `traefik:80/tcp` and `traefik:8443/tcp` correct. The owner-site fixture and `docs/ingress.md` state this
   consistently. No change needed.
2. **Listener contract coverage is sound.** `tests/modules/test_owner_site.py` now asserts `(name, published, target,
   protocol) → host_ip`, the module pins `${TAILSCALE_IP}:443:4443`, and `tests/modules/test_traefik.py` asserts
   `asDefault: tailnet` only, per-entry-point `forwardedHeaders.trustedIPs: []` and `aliasHeadersStrategy: delete`,
   one ACME resolver issuing RSA2048 on the ISRG Root X1 chain, and ACME storage on a bind so a recreate keeps
   certificates. `config/traefik/routes.example.yml` routers are asserted to resolve services and to put the
   identity-stripping definition first with empty values.
3. **Route validator input, not a cutover prerequisite.** Private routes use middleware **chains** whose first element
   resolves to the identity-stripping definition (`[strip, compress]`, `[strip, compress, forwardAuth]`), so a future
   validator that rejects all chains would reject the real site; bounded one-level chain expansion would suffice.
   Traefik regex-group `${n}` replacements are literal config, not Go templates. Nothing in Decision 0020 makes a
   release-time validator required for cutover acceptance, so it stays a separately gated safety improvement and must
   not be read as blocking the swap.
4. **`docs/operations.md` runbook: one real gap, now closed.** The draft never invents a marker for the *approved*
   model — `<approved-sha>` is an operator-held value and step 4 verifies `sha256sum current/edge.json` against it,
   which is sufficient. The manual apply path also never writes `state/edge-failed`, while the prose only said
   "record the rejected hash there". Fixed in this docs pass with a concrete command that derives the rejected hash
   from the retained `releases/current/edge.json` and writes it only when no marker exists, so an unrelated marker is
   never overwritten and no unbound variable is introduced.
5. **`docs/module-migration.md` historical/current ambiguity, fixed in this docs pass.** Its stage-2 step named
   "commit `f6d9fbf` plus the reviewed Caddy fix" as if it were still actionable. Both platform stages are merged
   history (2026-10-31), so the step now says a site adopting the catalog needs only the site PRs and host
   preparation, labels that era's proxy fix as historical, and points new adopters at
   [operations](../../operations.md) for the Traefik swap. The intentionally historical migration sequence is
   otherwise preserved.
6. **Environment prerequisite, not a product defect:** `scripts/ci/public-check.sh` runs `python3 -m pytest`, so the
   local host needs pytest installed on the interpreter it uses (the nox/gate environments provide it). No CI tooling
   change is proposed and no decision record is needed.
7. **`tests/host/test_edge_update.py` keeps one bounded rollback fixture using Caddy** ("the proxy swap: caddy holds
   the port"). That is intentional rollback coverage for the previous signed edge model; label it as such rather than
   removing it, and it stays until the cutover acceptance window closes.
8. **Cosmetic:** `noxfile.py` now has no blank line between the `images` session and the next decorator. Out of ruff's
   file scope, so no CI effect; fix opportunistically.
9. **Necessity pass over the changed public code (read-only greps in this worktree; the shared instruction to send
   Sonnet agents per file conflicts with this assignment's "do not spawn more agents", so the same checks were done
   locally):** every addition has a caller and no native substitute — `RELEASE_INPUTS_DIR_DEFAULT` is still used by the
   release-input test, so the import in `tests/release/test_workflow.py` is live; the two build-input parity tests cover
   opposite directions and neither is redundant; `_loopback_ports`, `_fetch` and `_UPSTREAM_MARKER` add real-traffic
   probes that the existing `_docker`/`_wait` helpers cannot express, using stdlib `socket`, `urllib` and `ssl`
   (`ssl._create_unverified_context` is deliberate: the shipped config's self-signed certificate);
   `test_setup_phase_names_resolve_to_platform_modules` guards a drift nothing enforces at runtime (`setup/engine/local.py`
   never reads the module catalogue, so a stale `phases.json` name is silently skipped); the `edge-failed` write in the
   runbook is necessary because only `host/homelab-edge-update` writes that marker and `homelabctl` has no such command;
   the removed `cap_drop_check` matrix field and its `noxfile.py` conditional have no remaining consumer anywhere except
   historical plan documents. Nothing in the diff is groundwork for an unbuilt feature beyond the two explicitly
   deferred deliverables (route validator, setup route generator).

## Proposed commit grouping and integration order (nothing staged)

1. `feat(edge): correct Traefik listener targets and exposure contract` —
   `tests/modules/owner_site/overrides/traefik.yml`, `tests/modules/test_owner_site.py`, `docs/security.md`,
   `docs/ingress.md` (Task 1 port half). Run `pytest tests/modules` first.
2. `test(release): probe every Traefik listener and default-router isolation` —
   `tests/release/test_render_docker.py`. Needs a Docker session to be green; pair with the sibling's Docker run.
3. `chore(setup): move phase selection to Traefik` —
   `setup/config/phases.json`, `setup/README.md`, `setup/tests/*`. Keep inside the 2,000-line setup budget;
   `nox -s wizard` must stay green.
4. `ci: drop dead build fields and replace the string-only reconcile check` —
   `.github/workflows/ci.yml`, `noxfile.py`, `tests/ci/test_workflow_security.py`,
   `tests/release/test_reconcile.py`, `tests/release/test_workflow.py`.
5. `docs: supersede Caddy records, record gate evidence, draft the proxy cutover` —
   all `docs/decisions/*` edits, `docs/decisions/README.md`, `docs/operations.md`, `docs/module-migration.md`
   relabelling, and the retirement plan's historical banner.
6. Merge order inside one PR or as adjacent PRs on `main`: 1 and 4 are independent; 5 must land with 1 so the guides
   never describe the retired proxy as current; 2 lands with the Docker run. The private site half and this platform
   half must be published as the **one** signed candidate recording both commits, with the previous signed edge model
   and pinned old-proxy image retained through the acceptance window.

Remaining work continues to be: re-fetching and pairing the exact signed platform and site revisions, live acceptance
checks (real ACME key and chain, Authentik group allow and deny, forged identity headers, actual-site listeners), and
the owner's operational approval. Decision 0020's gate table already reflects the VM run, with G10 partial; G2, G3
(issuance), G4, G6, G7 and G9 remain. A release-time route validator stays a separately gated safety improvement, not
on the path to cutover acceptance.

## Remaining work

1. Review the combined implementation before integration. Setup phase migration and catalogue behaviour have passed
   the focused tests and full wizard session; no phase-migration development remains known.
2. Keep ADR gate evidence current as site review and rehearsal finish. Historical rationale is retained;
   documentation and public-readiness checks have passed.
3. Listener probes: **prior-chat evidence only** (Docker Desktop rerun by a sibling agent: focused test 1 passed,
   full `test_render_docker.py` 5 passed, resources cleaned up). Not rerun by the public-integration pass; native
   Linux acceptance remains open.
4. Keep the setup route generator deferred; resume Task 3b only as a separate wizard feature.
5. Obtain explicit owner approval before implementing the release-time route validator (Task 2). Inspect actual private routes first to confirm the proposed parser syntax is sufficient; this is a separately gated safety improvement, not a cutover prerequisite.
6. Coordinate matching platform and private-site revisions. Render the exact pair and retain the previous signed edge model and pinned Caddy image for rollback.
7. G10 (swap and recovery) is now **proven in the isolated native-Linux VM** recorded at the top of this file: Caddy→Traefik
   swap, `observe-only` refusal, forced-unhealthy rollback to Caddy, rejected-hash retention and skip, a different
   candidate clearing that marker, and manual recovery from a healthy-but-wrong route using a separately retained
   previous model. Still open inside G10: real ACME key/chain survival (the VM used self-signed fixtures), actual-site
   acceptance and any production rehearsal. The earlier Docker Desktop attempt (other host, 0 scenarios) stays invalid
   evidence, superseded by the VM run.
8. Prepare the cutover runbook. Production requires separate owner operational approval; freeze automatic applies, wait for in-flight work, verify routes and Authentik behaviour, and keep rollback material through the acceptance window.
9. Focused checks, `scripts/dev/gate --fast`, complexity budget and public-readiness checks have passed. Run the full Docker-backed gate in an environment with Docker access, then report source implementation, disposable rehearsal and production acceptance separately.

## Known evidence gaps and gates

- `spike/traefik/FINDINGS.md` and `EVIDENCE.txt` at commit `bc1f937` were recovered from git history on
  2026-11-05. Decision 0020 now distinguishes that historical spike from current runtime and site acceptance.
- The private site repository and an existing Traefik migration checkout were found locally on 2026-11-05.
  The migration branch has diverged from its remote; preserve its existing work and review the exact revisions
  before rendering or coordinating changes. Private values stay outside this public repository.
- Read-only review found the site's listener targets and exposure declarations consistent. Its expanded middleware
  chains strip identity before forward authentication. The proposed validator's chain rejection would reject these
  routes: specify bounded chain support or change the site before implementing the validator. Traefik regex-group
  replacements are literal config, not Go templates. No authentication bypass was established by this review.
- One upstream is intentionally outside the selected catalogue; a read-only host check found it healthy and attached
  to the shared network. Catalogue absence alone is not a missing-service defect. Site tests were collected and the
  private cutover script syntax checked; neither establishes production acceptance.
- `docs/operations.md` now contains a draft proxy-only cutover procedure with signed candidate admission, retained
  recovery material, explicit proxy stops, manual rollback and success-only updater bookkeeping. Rehearsal and
  operational approval remain prerequisites for execution.
- Decision 0020's gate table already records **G10 as partial** from the isolated native-Linux VM run (swap, forced-unhealthy
  rollback, failed-hash skip, manual recovery; certificate fixtures self-signed, not production ACME stores). Still
  needing evidence for G2 runtime isolation on the real site, G3 real RSA/X1 issuance, G4 header stripping on the
  current site, G6 Authentik group allow/deny, G7 native-Linux source addresses and G9 Dependabot update evidence.
  Keep documented GATE-1/probe deferrals distinct from passed gates.
- The edge updater skips the hash of a failed release. If a healthy apply later proves semantically wrong, `edge-good.json` already contains the Traefik model. Preserve the previous signed bundle/model independently and keep automation paused during manual recovery.
- `observe-only` is checked when the edge updater starts; it does not stop an invocation already running. The cutover procedure must pause applies and wait for active work before touching proxy containers.

## Qwen agent setup and permission handling

Paseo's configured Qwen 3.8 Flash Coder model was validated with a read-only setup test task, then used for the listener probe implementation. Auto mode worked for both tasks. Keep permission prompts under owner control; do not use blanket bypass or approve prompts on the owner's behalf.

## Constraints

- No private domains, IPs, home paths, plugin names or secrets in this public repository.
- Keep images digest-pinned and Actions SHA-pinned; secrets remain files under `${HOMELAB_SECRET_DIR}`.
- Do not let the controller apply edge services; the edge updater owns edge applies.
- Do not raise line or complexity budgets; keep `C901 <= 10` and setup under 2,000 lines.
- Do not run production cutover or restart services without the owner's operational approval and reviewed runbook.
