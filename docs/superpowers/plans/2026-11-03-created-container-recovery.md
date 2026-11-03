# Created Container Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a newer signed release start managed containers left in Docker's `Created` state by an earlier failed apply.

**Architecture:** Extend the existing `plan()` classification so a `Created` container requested as running enters `Plan.recreate`, even when its config hash matches. Reuse the existing snapshot, Compose `up --no-deps`, health and pointer-advance path. Preserve owner-stopped `Exited` containers and site-configured stopped services.

**Tech Stack:** Python 3, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-03-created-container-recovery-design.md`

## Global Constraints

- Apply only app services in the signed release's `managed` set; edge stays with its updater.
- Keep `--no-deps`, the owner-stop rule, and the current `DEGRADED` failure path.
- Do not add a new controller mechanism or raise `config/ci/budgets.json`.
- Update the relevant operations guide in the same change.

## Review Focus

- Matching hash, `Created`, site running: start and health-check it.
- Matching hash, `Created`, site stopped: leave it unstarted.
- Matching hash, `Exited`, site running: retain the owner-stop notice.
- Matching hash, `running`, site running: no operation.
- Changed hash, `Created`, site running: start with the desired configuration.

---

### Task 1: Classify Created containers

**Files:**
- Modify: `scripts/controller/planner.py` (`plan()` app-service loop)
- Test: `tests/controller/test_planner.py`
- Test: `tests/controller/test_reconcile.py`
- Modify: `docs/operations.md` (release recovery guidance)

**Interfaces:** `plan(release_dir: Path | str, adapter: DockerAdapter) -> Plan`; `ContainerView(config_hash, status, error)`; no new public interface.

- [ ] **Step 1: Write failing planner tests.** Add parameterised cases for the five states in Review Focus. Assert the full `Plan`, including empty notices and failures where appropriate.
- [ ] **Step 2: Run the new planner cases.** Use `scripts/dev/gate --fast -s controller` or the controller nox environment. Confirm the `Created`/running/matching case fails because `recreate` is empty; the existing `Exited` and `running` cases remain green.
- [ ] **Step 3: Write a failing reconcile test.** Supply a fake adapter whose managed service is `ContainerView("same", "created", "")`, and a materialised release with the same hash. Assert Compose `up` and `healthy` are called before `current` and `highwater` advance. Confirm it fails on current code.
- [ ] **Step 4: Implement the planner change.** In the app-service loop, classify `Created` plus site running as `recreate` even if hashes match. Keep the `Exited` notice check ahead of it. Keep site-stopped `Created` unchanged when its hash matches.
- [ ] **Step 5: Run controller tests.** Run `scripts/dev/gate --fast -s controller`; confirm all planner and reconcile tests pass.
- [ ] **Step 6: Update operations guidance.** Explain that a later signed release retries a managed `Created` container, while `Exited` remains owner-stopped. Keep host-specific incident details out of the public repo.
- [ ] **Step 7: Run the full gate and review the diff.** Run `scripts/dev/gate`, `git diff --check`, and inspect the complexity budget result. Confirm all sessions pass, then commit the code, tests and guide together.

## Integration

Open a PR against `main` after the full gate passes. The owner reviews and merges it; do not deploy from the checkout. After the next signed release, verify that `homelabctl status` is clear and each managed service requested as running is actually running and healthy.
