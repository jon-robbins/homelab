# Recover containers left in Created state

## Intent

A failed Compose start can leave a managed app container in Docker's `Created` state. Its configuration hash may already match a later signed release. The controller must not mark that later release applied while a service requested as running has never started.

## Decision

When a managed app service is configured as running, plan a Compose `up -d --no-deps` if its container is `Created`, even when the hash matches. Preserve the existing owner-stop rule for `Exited` containers and the existing no-start behaviour for services configured as stopped. Keep the change inside the current planner and Compose command path.

## Approaches

- **Plan the Created service for startup (chosen):** reuses the existing apply, snapshot and health gates. A subsequent signed release can recover it automatically.
- **Mark Created as a plan failure:** alerts but leaves a service that has never run waiting for manual intervention. It would require a separate recovery path and adds no safety once the dependency is healthy.

## Constraints

Only the controller applies app services from a verified signed release. It always passes `--no-deps`, never starts an owner-stopped `Exited` service, and never restores data automatically. Do not add state files or raise the controller line budget.

## Verification

Planner tests must distinguish `Created` and `Exited` with matching hashes for running and stopped site states. A controller test must show a Created service reaches Compose `up` and health gating before the release pointer advances. Run the full `scripts/dev/gate` before pushing.
