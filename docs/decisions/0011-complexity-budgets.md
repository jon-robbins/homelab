# 0011: Complexity budgets with a kill switch

- **Status:** Accepted (2026-10-15; controller cap amended 2026-10-17)
- **Code:** `config/ci/budgets.json`, `scripts/ci/`, `.pre-commit-config.yaml` (ruff `C901`)
- **Related:** [0005](0005-slim-controller.md)

## Context

The first version grew by accretion: every incident added a mechanism, no mechanism was ever removed, and most of
the code was written by LLMs that never saw the whole system. It ended up too large for anyone to hold in their
head.

## Decision

- CI enforces line budgets per area (`config/ci/budgets.json`: controller 750, renderer and module checks 600,
  `host/` 500), measured after `ruff format`, and ruff `C901` complexity ≤ 10 per function.
- When work does not fit, cut features. Budgets change only by an owner decision recorded here, never inside a
  feature change.

## Don't

- Don't compress code (long lines, dense one-liners) to fit a budget; that is a defect, not a fix.
- Don't raise a budget in the same change that needs the room.
