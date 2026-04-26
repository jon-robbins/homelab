#!/usr/bin/env bash
# Watch CI for the current branch: poll until complete, then show failed logs.
# Usage: scripts/ci-watch.sh [--run-id <id>]
set -euo pipefail

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "jon-robbins/homelab")"
POLL_INTERVAL=8

run_id="${1:-}"
if [[ "$run_id" == "--run-id" ]]; then
  run_id="${2:-}"
fi

# ── Resolve run ──────────────────────────────────────────────────────────────
if [[ -z "$run_id" ]]; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  echo "Watching latest CI run on branch: ${BRANCH}"
  run_id="$(gh run list --branch "${BRANCH}" --limit 1 --json databaseId -q '.[0].databaseId')"
fi

echo "Run ID: ${run_id}  (https://github.com/${REPO}/actions/runs/${run_id})"

# ── Poll until done ──────────────────────────────────────────────────────────
while true; do
  data="$(gh run view "${run_id}" --json status,conclusion,name,createdAt)"
  status="$(echo "$data" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])")"
  conclusion="$(echo "$data" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('conclusion') or '')")"

  if [[ "$status" == "completed" ]]; then
    break
  fi
  echo "  [$(date +%H:%M:%S)] status=${status} — waiting..."
  sleep "${POLL_INTERVAL}"
done

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Run ${run_id} finished: ${conclusion}"
echo "════════════════════════════════════════════════════════"
echo ""

if [[ "$conclusion" == "success" ]]; then
  echo "✓ All jobs passed."
  exit 0
fi

# ── Show failed logs ─────────────────────────────────────────────────────────
echo "Failed job logs:"
echo "────────────────────────────────────────────────────────"
gh run view "${run_id}" --log-failed 2>&1 \
  | grep -v '^$' \
  | sed 's/^[^\t]*\t[^\t]*\t//'   # strip "job\tstep\t" prefix for readability
