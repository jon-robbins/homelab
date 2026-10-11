#!/usr/bin/env bash
# shellcheck disable=SC2016
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/ops/refactor-agentic-runner.sh [options]

Runs the agentic refactor workflow with fresh Codex sessions per component phase.
Each component runs in its own Git worktree and implementation branch.

Options:
  --components <ids>       Comma-separated component ids. Default: all manifest components.
  --phases <phases>        Comma-separated phases. Default: audit,plan,evaluate,implement,verify,review,handoff.
                           Valid phases: audit, plan, evaluate, implement, verify, review, handoff.
  --max-iterations <n>     Max implement/verify/review cycles per component. Default: 3.
  --base-branch <branch>   Base branch for component worktrees. Default: manifest default_base_branch.
  --worktree-root <dir>    Directory for component worktrees. Default: /tmp/homelab-refactor-agentic-worktrees.
  --log-root <dir>         Directory for Codex prompt/output logs. Default: /tmp/homelab-refactor-agentic-logs.
  --run-id <id>            Run id used in artifact paths. Default: UTC timestamp.
  --codex-bin <path>       Codex CLI binary. Default: codex.
  --model <model>          Optional Codex model override.
  --audit-mode <mode>      Audit execution mode: segmented or single. Default: segmented.
  --phase-timeout-seconds <n>
                           Max wall-clock seconds per Codex worker. Default: 1800.
  --max-log-bytes <n>      Stop after a worker if its raw log exceeds this size. Default: 2000000.
  --stream-logs            Stream full Codex worker logs to stdout. Default: show only tails.
  --log-tail-lines <n>     Lines to show after each non-streamed worker. Default: 80.
  --approval-policy <mode> Codex approval policy. Default: never.
  --sandbox <mode>         Codex sandbox. Default: workspace-write.
  --persist-sessions       Do not pass --ephemeral to codex exec.
  --allow-dirty-existing   Allow reuse of dirty existing component worktrees.
  --dry-run                Print planned work without creating worktrees or running Codex.
  --yes                    Skip the start confirmation prompt.
  -h, --help               Show this help.

Examples:
  scripts/ops/refactor-agentic-runner.sh --yes
  scripts/ops/refactor-agentic-runner.sh --components system-inventory --phases audit,handoff --yes
  scripts/ops/refactor-agentic-runner.sh --components system-inventory --audit-mode single --yes
  scripts/ops/refactor-agentic-runner.sh --components media-agent-python --max-iterations 5 --yes
USAGE
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
MANIFEST_REL="docs/superpowers/refactor-component-manifest.yml"
MANIFEST="$ROOT/$MANIFEST_REL"
WORKFLOW_REL="docs/superpowers/refactor-agentic-workflow.md"
HITRACKER_REL="docs/superpowers/codex-human-in-the-loop.md"
AGENTS_REL="AGENTS.md"

RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
COMPONENT_FILTER=""
PHASES="audit,plan,evaluate,implement,verify,review,handoff"
MAX_ITERATIONS=3
BASE_BRANCH=""
WORKTREE_ROOT="${WORKTREE_ROOT:-${TMPDIR:-/tmp}/homelab-refactor-agentic-worktrees}"
LOG_ROOT="${LOG_ROOT:-${TMPDIR:-/tmp}/homelab-refactor-agentic-logs}"
CODEX_BIN="${CODEX_BIN:-codex}"
MODEL="${CODEX_MODEL:-}"
AUDIT_MODE="${REFACTOR_AUDIT_MODE:-segmented}"
PHASE_TIMEOUT_SECONDS="${REFACTOR_PHASE_TIMEOUT_SECONDS:-1800}"
MAX_LOG_BYTES="${REFACTOR_MAX_LOG_BYTES:-2000000}"
STREAM_LOGS=0
LOG_TAIL_LINES="${REFACTOR_LOG_TAIL_LINES:-80}"
CONTEXT_MAX_FILE_BYTES="${REFACTOR_CONTEXT_MAX_FILE_BYTES:-40000}"
CONTEXT_MAX_LINES_PER_FILE="${REFACTOR_CONTEXT_MAX_LINES_PER_FILE:-220}"
APPROVAL_POLICY="${CODEX_APPROVAL_POLICY:-never}"
SANDBOX="${CODEX_SANDBOX:-workspace-write}"
EPHEMERAL=1
ALLOW_DIRTY_EXISTING=0
DRY_RUN=0
YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --components)
      [[ $# -ge 2 ]] || die "--components requires a value"
      COMPONENT_FILTER="$2"
      shift 2
      ;;
    --phases)
      [[ $# -ge 2 ]] || die "--phases requires a value"
      PHASES="$2"
      shift 2
      ;;
    --max-iterations)
      [[ $# -ge 2 ]] || die "--max-iterations requires a value"
      MAX_ITERATIONS="$2"
      shift 2
      ;;
    --base-branch)
      [[ $# -ge 2 ]] || die "--base-branch requires a value"
      BASE_BRANCH="$2"
      shift 2
      ;;
    --worktree-root)
      [[ $# -ge 2 ]] || die "--worktree-root requires a value"
      WORKTREE_ROOT="$2"
      shift 2
      ;;
    --log-root)
      [[ $# -ge 2 ]] || die "--log-root requires a value"
      LOG_ROOT="$2"
      shift 2
      ;;
    --run-id)
      [[ $# -ge 2 ]] || die "--run-id requires a value"
      RUN_ID="$2"
      shift 2
      ;;
    --codex-bin)
      [[ $# -ge 2 ]] || die "--codex-bin requires a value"
      CODEX_BIN="$2"
      shift 2
      ;;
    --model)
      [[ $# -ge 2 ]] || die "--model requires a value"
      MODEL="$2"
      shift 2
      ;;
    --audit-mode)
      [[ $# -ge 2 ]] || die "--audit-mode requires a value"
      AUDIT_MODE="$2"
      shift 2
      ;;
    --phase-timeout-seconds)
      [[ $# -ge 2 ]] || die "--phase-timeout-seconds requires a value"
      PHASE_TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --max-log-bytes)
      [[ $# -ge 2 ]] || die "--max-log-bytes requires a value"
      MAX_LOG_BYTES="$2"
      shift 2
      ;;
    --stream-logs)
      STREAM_LOGS=1
      shift
      ;;
    --log-tail-lines)
      [[ $# -ge 2 ]] || die "--log-tail-lines requires a value"
      LOG_TAIL_LINES="$2"
      shift 2
      ;;
    --approval-policy)
      [[ $# -ge 2 ]] || die "--approval-policy requires a value"
      APPROVAL_POLICY="$2"
      shift 2
      ;;
    --sandbox)
      [[ $# -ge 2 ]] || die "--sandbox requires a value"
      SANDBOX="$2"
      shift 2
      ;;
    --persist-sessions)
      EPHEMERAL=0
      shift
      ;;
    --allow-dirty-existing)
      ALLOW_DIRTY_EXISTING=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --yes)
      YES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

[[ -f "$MANIFEST" ]] || die "manifest not found: $MANIFEST_REL"
[[ "$MAX_ITERATIONS" =~ ^[1-9][0-9]*$ ]] || die "--max-iterations must be a positive integer"
[[ "$AUDIT_MODE" == "segmented" || "$AUDIT_MODE" == "single" ]] ||
  die "--audit-mode must be segmented or single"
[[ "$PHASE_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] ||
  die "--phase-timeout-seconds must be a positive integer"
[[ "$MAX_LOG_BYTES" =~ ^[1-9][0-9]*$ ]] ||
  die "--max-log-bytes must be a positive integer"
[[ "$LOG_TAIL_LINES" =~ ^[1-9][0-9]*$ ]] ||
  die "--log-tail-lines must be a positive integer"
[[ "$CONTEXT_MAX_FILE_BYTES" =~ ^[1-9][0-9]*$ ]] ||
  die "REFACTOR_CONTEXT_MAX_FILE_BYTES must be a positive integer"
[[ "$CONTEXT_MAX_LINES_PER_FILE" =~ ^[1-9][0-9]*$ ]] ||
  die "REFACTOR_CONTEXT_MAX_LINES_PER_FILE must be a positive integer"

manifest_default_base_branch() {
  awk -F': ' '/^default_base_branch:/ {print $2; exit}' "$MANIFEST"
}

manifest_component_ids() {
  awk '/^  - id: / {print $3}' "$MANIFEST"
}

manifest_value() {
  local id="$1"
  local key="$2"
  awk -v id="$id" -v key="$key" '
    /^  - id: / { active = ($3 == id) }
    active {
      prefix = "    " key ": "
      if (index($0, prefix) == 1) {
        value = substr($0, length(prefix) + 1)
        gsub(/^"/, "", value)
        gsub(/"$/, "", value)
        print value
        exit
      }
    }
  ' "$MANIFEST"
}

component_exists() {
  local id="$1"
  local found
  found="$(manifest_value "$id" prompt || true)"
  [[ -n "$found" ]]
}

split_csv() {
  local input="$1"
  local -n output_ref="$2"
  local item

  IFS=',' read -r -a output_ref <<< "$input"
  for item in "${output_ref[@]}"; do
    [[ -n "$item" ]] || die "empty item in comma-separated value: $input"
  done
}

declare -a SELECTED_PHASES
declare -a SELECTED_COMPONENTS
split_csv "$PHASES" SELECTED_PHASES

valid_phase() {
  case "$1" in
    audit|plan|evaluate|implement|verify|review|handoff) return 0 ;;
    *) return 1 ;;
  esac
}

phase_enabled() {
  local wanted="$1"
  local phase

  for phase in "${SELECTED_PHASES[@]}"; do
    [[ "$phase" == "$wanted" ]] && return 0
  done
  return 1
}

for phase in "${SELECTED_PHASES[@]}"; do
  valid_phase "$phase" || die "invalid phase: $phase"
done

if [[ -z "$BASE_BRANCH" ]]; then
  BASE_BRANCH="$(manifest_default_base_branch)"
fi
[[ -n "$BASE_BRANCH" ]] || die "base branch not found in manifest"

if [[ -n "$COMPONENT_FILTER" ]]; then
  split_csv "$COMPONENT_FILTER" SELECTED_COMPONENTS
else
  mapfile -t SELECTED_COMPONENTS < <(manifest_component_ids)
fi

[[ "${#SELECTED_COMPONENTS[@]}" -gt 0 ]] || die "no components selected"

for component in "${SELECTED_COMPONENTS[@]}"; do
  component_exists "$component" || die "component not found in manifest: $component"
done

if [[ "$DRY_RUN" -eq 0 ]]; then
  command -v "$CODEX_BIN" >/dev/null 2>&1 || die "Codex CLI not found: $CODEX_BIN"
  command -v timeout >/dev/null 2>&1 || die "timeout command not found"
fi

branch_checked_out_elsewhere() {
  local branch="$1"

  git -C "$ROOT" worktree list --porcelain |
    awk -v branch_ref="refs/heads/$branch" '
      $1 == "branch" && $2 == branch_ref { found = 1 }
      END { exit found ? 0 : 1 }
    '
}

ensure_component_worktree() {
  local component="$1"
  local branch="$2"
  local worktree="$WORKTREE_ROOT/$component"
  local current_branch

  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '%s\n' "$worktree"
    return 0
  fi

  mkdir -p "$WORKTREE_ROOT"

  if [[ -e "$worktree" ]]; then
    git -C "$worktree" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
      die "existing path is not a git worktree: $worktree"
    current_branch="$(git -C "$worktree" branch --show-current)"
    [[ "$current_branch" == "$branch" ]] ||
      die "existing worktree $worktree is on $current_branch, expected $branch"
  else
    git -C "$ROOT" rev-parse --verify "$BASE_BRANCH^{commit}" >/dev/null ||
      die "base branch not found: $BASE_BRANCH"

    if git -C "$ROOT" show-ref --verify --quiet "refs/heads/$branch"; then
      if branch_checked_out_elsewhere "$branch"; then
        die "branch is already checked out in another worktree: $branch"
      fi
      git -C "$ROOT" worktree add "$worktree" "$branch"
    else
      git -C "$ROOT" worktree add -b "$branch" "$worktree" "$BASE_BRANCH"
    fi
  fi

  if [[ "$ALLOW_DIRTY_EXISTING" -eq 0 ]] &&
    [[ -n "$(git -C "$worktree" status --porcelain)" ]] &&
    [[ ! -d "$worktree/docs/superpowers/refactor-runs/$RUN_ID/$component" ]]; then
    die "worktree has pre-existing dirty state: $worktree (use --allow-dirty-existing to override)"
  fi

  printf '%s\n' "$worktree"
}

component_artifact_rel() {
  local component="$1"
  printf 'docs/superpowers/refactor-runs/%s/%s\n' "$RUN_ID" "$component"
}

state_file_path() {
  local worktree="$1"
  local component="$2"
  printf '%s/%s/runner-state.env\n' "$worktree" "$(component_artifact_rel "$component")"
}

read_state_value() {
  local state_file="$1"
  local key="$2"

  [[ -f "$state_file" ]] || return 1
  awk -F'=' -v key="$key" '$1 == key { value = substr($0, length(key) + 2) } END { if (value != "") print value }' "$state_file"
}

safe_context_path() {
  local path="$1"

  case "$path" in
    ""|data/*|.git/*|.env|.env.*|*.log|logs/*|*.tmp|*.swp|*.bak|*.bak.*)
      return 1
      ;;
    docs/images/*|*.svg|*.png|*.jpg|*.jpeg|*.gif|*.webp|*.ico)
      return 1
      ;;
    config/caddy/caddy)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

context_focus_description() {
  local component="$1"
  local segment="$2"

  case "$component:$segment" in
    system-inventory:compose-services)
      printf '%s\n' "Compose service catalog: includes, service roles, networks, ports, volumes, env, and host-mode exceptions."
      ;;
    system-inventory:routes-access)
      printf '%s\n' "Ingress and access: Caddy routes, HTTP/HTTPS split, LAN/Tailscale/WAN assumptions, Cloudflare, nginx SNI, and VPN surfaces."
      ;;
    system-inventory:env-contract)
      printf '%s\n' "Environment contract: .env.example versus compose, setup scripts, app settings, and integration-test env usage."
      ;;
    system-inventory:tests-ci)
      printf '%s\n' "Validation map: CI workflows, compose/static tests, runtime smoke tests, integration tests, and deploy gates."
      ;;
    system-inventory:docs-drift)
      printf '%s\n' "Documentation drift: README and operational docs versus current compose, Caddy, VPN, CI, and testing behavior."
      ;;
    system-inventory:apps-workers)
      printf '%s\n' "Application and worker architecture: media-agent modules, homelab-workers package, API/router/action boundaries, and test coverage."
      ;;
    *)
      printf '%s\n' "Bounded context for the selected component and phase. Use this as the starting point before targeted inspection."
      ;;
  esac
}

context_files_for() {
  local component="$1"
  local segment="$2"

  case "$component:$segment" in
    system-inventory:compose-services)
      printf '%s\n' \
        docker-compose.yml compose/base.yml compose/network.yml compose/arr.yml compose/media.yml \
        compose/download.yml compose/workers.yml compose/autobrr.yml compose/files.yml compose/llm.yml \
        config/external-tunnel/docker-compose.yml .env.example README.md docs/network-access.md scripts/README.md
      ;;
    system-inventory:routes-access)
      printf '%s\n' \
        config/caddy/Caddyfile config/caddy/Dockerfile \
        config/cloudflared/config.yml.example config/cloudflared/README.md \
        config/external-tunnel/docker-compose.yml config/external-tunnel/README.md config/external-tunnel/SETUP.md \
        docs/caddy-guide.md docs/network-access.md docs/direct-media-8443-ingress.md \
        docs/xray-fallback-canary.md docs/external-tunnel-vpn-setup.md compose/network.yml README.md
      ;;
    system-inventory:env-contract)
      printf '%s\n' \
        .env.example docker-compose.yml compose/base.yml compose/network.yml compose/arr.yml compose/media.yml \
        compose/download.yml compose/workers.yml compose/autobrr.yml compose/files.yml compose/llm.yml \
        config/external-tunnel/docker-compose.yml scripts/ops/setup.sh tests/compose/test_env_completeness.sh \
        apps/media-agent/app/config.py apps/media-agent/app/core/config.py \
        apps/homelab-workers/src/homelab_workers/integration/bazarr_subtitles.py README.md
      ;;
    system-inventory:tests-ci)
      printf '%s\n' \
        .github/workflows/ci.yml .github/workflows/nightly-deploy.yml .github/workflows/render-md-mermaid.yml \
        scripts/ci/ci-local.sh scripts/ci/ci-watch.sh scripts/ci/gh-actions-local.sh \
        tests/compose/run_tests.sh tests/compose/test_yaml_syntax.sh tests/compose/test_env_completeness.sh \
        tests/compose/test_no_hardcoded_paths.sh tests/compose/test_caddy_labels.sh \
        tests/compose/test_network_definitions.sh tests/compose/test_host_mode_whitelist.sh \
        tests/compose/test_host_ports_available.sh tests/runtime/run_tests.sh \
        tests/integration/README.md tests/integration/router-smoke-p0.sh \
        tests/integration/bazarr-e2e.sh README.md scripts/README.md
      ;;
    system-inventory:docs-drift)
      printf '%s\n' \
        README.md scripts/README.md docs/network-access.md docs/caddy-guide.md docs/service-troubleshooting.md \
        docs/direct-media-8443-ingress.md docs/xray-fallback-canary.md \
        docs/xray-china-performance.md docs/external-tunnel-vpn-setup.md docs/prowlarr-caddy-routing.md \
        tests/integration/README.md config/cloudflared/README.md
      ;;
    system-inventory:apps-workers)
      printf '%s\n' \
        apps/media-agent/pyproject.toml apps/media-agent/requirements.txt apps/media-agent/requirements-dev.txt \
        apps/media-agent/app/README.md apps/media-agent/app/main.py apps/media-agent/app/config.py \
        apps/media-agent/app/core/README.md apps/media-agent/app/actions/README.md \
        apps/media-agent/app/api/README.md apps/media-agent/app/router/README.md \
        apps/media-agent/app/integrations/README.md apps/media-agent/app/services/README.md \
        apps/homelab-workers/pyproject.toml \
        apps/homelab-workers/src/homelab_workers/integration/bazarr_subtitles.py \
        apps/homelab-workers/src/homelab_workers/shared/qbittorrent.py \
        apps/homelab-workers/src/homelab_workers/shared/arr.py README.md
      ;;
    compose-orchestration:*)
      printf '%s\n' \
        docker-compose.yml compose/base.yml compose/network.yml compose/arr.yml compose/media.yml \
        compose/download.yml compose/workers.yml compose/autobrr.yml compose/files.yml compose/llm.yml .env.example \
        tests/compose/run_tests.sh tests/compose/test_yaml_syntax.sh tests/compose/test_env_completeness.sh \
        tests/compose/test_no_hardcoded_paths.sh tests/compose/test_network_definitions.sh \
        tests/compose/test_host_mode_whitelist.sh tests/compose/test_host_ports_available.sh \
        scripts/ops/setup.sh .github/workflows/ci.yml .github/workflows/nightly-deploy.yml README.md
      ;;
    ingress-caddy-dns-vpn:*)
      printf '%s\n' \
        config/caddy/Caddyfile config/caddy/Dockerfile compose/network.yml \
        config/external-tunnel/docker-compose.yml config/external-tunnel/README.md \
        config/external-tunnel/SETUP.md config/cloudflared/config.yml.example config/cloudflared/README.md \
        scripts/ops/test-caddy-wan-paths.sh \
        scripts/ops/rollback-xray-443-fallbacks.sh \
        scripts/ops/clear-xray-fallbacks.sh scripts/ops/media-dns.sh scripts/tunnel/xray-test.sh \
        scripts/tunnel/test-xray-client.sh docs/caddy-guide.md docs/network-access.md docs/direct-media-8443-ingress.md \
        docs/xray-fallback-canary.md README.md
      ;;
    media-agent-python:*)
      git -C "$ROOT" ls-files 'apps/media-agent/**' 'compose/media.yml' 'compose/workers.yml' 'README.md' 'docs/**/*.md'
      ;;
    homelab-workers-e2e:*)
      git -C "$ROOT" ls-files 'apps/homelab-workers/**' 'tests/integration/**' 'tests/runtime/**' \
        '.github/workflows/nightly-deploy.yml' 'scripts/ci/ci-local.sh' 'docs/**/*.md' 'README.md'
      ;;
    ops-scripts-ci-cd:*)
      git -C "$ROOT" ls-files 'scripts/**' 'tests/compose/**' 'tests/integration/**' 'tests/runtime/**' \
        '.github/workflows/**' 'docs/**/*.md' 'README.md'
      ;;
    docs-diagrams-runbooks:*|synthesis-roadmap:*)
      git -C "$ROOT" ls-files 'README.md' 'docs/**/*.md' 'scripts/README.md' 'tests/integration/README.md' \
        'apps/**/README.md' '.github/workflows/render-md-mermaid.yml'
      ;;
    *)
      git -C "$ROOT" ls-files 'README.md' 'docs/**/*.md' 'compose/**' 'config/**' 'scripts/**' 'tests/**'
      ;;
  esac
}

append_file_excerpt() {
  local worktree="$1"
  local rel="$2"
  local file="$worktree/$rel"
  local bytes
  local lines

  [[ -f "$file" ]] || {
    printf '### `%s`\n\nMissing in this worktree.\n\n' "$rel"
    return
  }
  safe_context_path "$rel" || {
    printf '### `%s`\n\nExcluded from context pack by safety rules.\n\n' "$rel"
    return
  }

  bytes="$(wc -c < "$file" | tr -d ' ')"
  lines="$(wc -l < "$file" | tr -d ' ')"
  printf '### `%s`\n\n' "$rel"
  printf '- Size: %s bytes, %s lines\n' "$bytes" "$lines"
  if (( bytes > CONTEXT_MAX_FILE_BYTES )); then
    printf -- '- Included excerpt: first %s lines only because file exceeds context byte cap.\n\n' "$CONTEXT_MAX_LINES_PER_FILE"
  else
    printf -- '- Included excerpt: up to %s lines.\n\n' "$CONTEXT_MAX_LINES_PER_FILE"
  fi
  printf '```text\n'
  sed -n "1,${CONTEXT_MAX_LINES_PER_FILE}p" "$file"
  printf '\n```\n\n'
}

generate_context_pack() {
  local worktree="$1"
  local component="$2"
  local segment="$3"
  local phase="$4"
  local prompt_rel="$5"
  local context_rel="$6"
  local context_path="$worktree/$context_rel"
  local artifact_rel
  local file
  local filtered_files

  artifact_rel="$(component_artifact_rel "$component")"
  mkdir -p "$(dirname "$context_path")"

  filtered_files="$(mktemp "${TMPDIR:-/tmp}/refactor-context-files.XXXXXX")"
  {
    printf '%s\n' "$AGENTS_REL" "$WORKFLOW_REL" "$HITRACKER_REL" "$MANIFEST_REL" "$prompt_rel"
    context_files_for "$component" "$segment"
    if [[ -d "$worktree/$artifact_rel" ]]; then
      (
        cd "$worktree"
        find "$artifact_rel" -maxdepth 3 -type f \( -name '*.md' -o -name '*.env' \) -print
      )
    fi
  } | awk 'NF && !seen[$0]++' > "$filtered_files"

  {
    printf '# Refactor Context Pack\n\n'
    printf '- Run id: `%s`\n' "$RUN_ID"
    printf '- Component: `%s`\n' "$component"
    printf '- Segment: `%s`\n' "$segment"
    printf '- Phase: `%s`\n' "$phase"
    printf '- Generated: `%s`\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '## Focus\n\n'
    context_focus_description "$component" "$segment"
    printf '\n## Context Discipline\n\n'
    printf -- '- Treat this file as the bounded starting context.\n'
    printf -- '- Prefer citing paths from this pack before opening more files.\n'
    printf -- '- If more inspection is required, read only targeted text files and summarize findings; do not print entire files to stdout.\n'
    printf -- '- Do not inspect excluded generated/binary/runtime paths such as `data/**`, `docs/images/**`, `config/caddy/caddy`, logs, or local `.env` files.\n\n'
    printf '## Selected File Index\n\n'
    while IFS= read -r file; do
      safe_context_path "$file" || continue
      [[ -f "$worktree/$file" ]] || continue
      printf -- '- `%s`\n' "$file"
    done < "$filtered_files"
    printf '\n## Filtered Repo Index\n\n'
    git -C "$worktree" ls-files |
      while IFS= read -r file; do
        safe_context_path "$file" || continue
        printf -- '- `%s`\n' "$file"
      done
    printf '\n## Selected File Excerpts\n\n'
    while IFS= read -r file; do
      safe_context_path "$file" || continue
      append_file_excerpt "$worktree" "$file"
    done < "$filtered_files"
  } > "$context_path"

  rm -f "$filtered_files"
}

write_phase_prompt() {
  local prompt_file="$1"
  local component="$2"
  local title="$3"
  local mode="$4"
  local risk="$5"
  local branch="$6"
  local prompt_rel="$7"
  local phase="$8"
  local iteration="$9"
  local artifact_rel="${10}"
  local context_rel="${11}"
  local state_rel="$artifact_rel/runner-state.env"

  cat > "$prompt_file" <<PROMPT
You are a fresh Codex agent session for the homelab agentic refactor workflow.

Repository root: current Codex working root supplied by the runner with -C
Run id: $RUN_ID
Component id: $component
Component title: $title
Component mode: $mode
Component risk: $risk
Phase: $phase
Iteration: $iteration
Implementation branch: $branch
Component prompt: $prompt_rel
Artifact directory: $artifact_rel
Runner state file: $state_rel
Context pack: $context_rel

Mandatory source files:
- $AGENTS_REL
- $WORKFLOW_REL
- $HITRACKER_REL
- $MANIFEST_REL
- $prompt_rel
- $context_rel

Hard rules:
- This session handles only the component and phase named above.
- Do not carry work into another component.
- Read the context pack first. Then read only the mandatory source files and targeted text files needed for this phase.
- Use installed Superpowers skills by name when available.
- Do not read or edit ~/.superpowers-mcp.
- Do not read generated/binary/runtime paths such as data/**, docs/images/**, config/caddy/caddy, logs, .env, or .env.*.
- Do not run broad repo-wide searches that include generated assets; scope searches to text paths listed in the context pack.
- Do not print entire file contents to stdout. Summarize evidence in artifacts and cite repo paths.
- Confirm the current branch is "$branch" before mutating repository files.
- Stay inside the allowed scope and approval gates from $MANIFEST_REL.
- Do not perform Docker, live service, Caddy reload, DNS, Cloudflare, router, firewall, VPN, Xray, nginx, Tailscale, live media, credential, or destructive Git operations.
- If a needed action requires human approval, stop the phase and write COMPONENT_STATUS=NEEDS_APPROVAL to $state_rel.
- If scope, branch, dependencies, or validation are unclear, stop the phase and write COMPONENT_STATUS=BLOCKED to $state_rel.
- For implementation work, run required static/unit validation and an approved smoke test, or record a specific no-smoke-test-applicable rationale.
- Keep generated artifacts under $artifact_rel unless the workflow explicitly requires a repo-standard docs path.

At the end of this phase, create or update $state_rel with these exact keys:
COMPONENT_STATUS=<CONTINUE|DONE|BLOCKED|NEEDS_APPROVAL|FAILED>
LAST_PHASE=$phase
NEXT_ACTION=<short next action>

Phase instructions:
PROMPT

  case "$phase" in
    audit)
      cat >> "$prompt_file" <<'PROMPT'
- Run the component audit prompt only.
- Do not implement changes.
- Save the audit using docs/superpowers/templates/audit-output.md as audit-output.md in the artifact directory.
- Set COMPONENT_STATUS=CONTINUE unless the component mode is audit-only and the audit is complete; for audit-only, set COMPONENT_STATUS=DONE.
PROMPT
      ;;
    plan)
      cat >> "$prompt_file" <<'PROMPT'
- Read audit-output.md from the artifact directory.
- Use superpowers:writing-plans when available.
- Write a PR-sized implementation plan using docs/superpowers/templates/implementation-plan.md.
- The plan must name the implementation branch, required validations, and a smoke test or no-smoke-test-applicable rationale.
- Do not implement changes.
- Set COMPONENT_STATUS=CONTINUE unless planning is blocked.
PROMPT
      ;;
    evaluate)
      cat >> "$prompt_file" <<'PROMPT'
- Evaluate implementation-plan.md using docs/superpowers/templates/plan-evaluation.md.
- Check allowed scope, forbidden scope, branch isolation, dependencies, approval gates, validations, and smoke-test coverage.
- Reject plans that lack concrete tests, smoke testing, branch isolation, or approval gates.
- Do not implement changes.
- Set COMPONENT_STATUS=CONTINUE only if the plan is accepted; otherwise set COMPONENT_STATUS=BLOCKED.
PROMPT
      ;;
    implement)
      cat >> "$prompt_file" <<'PROMPT'
- Read implementation-plan.md and plan-evaluation.md from the artifact directory.
- Proceed only if the evaluation status is accepted.
- Implement the next accepted task for this component only. If the accepted plan is PR-sized and has one coherent implementation batch, complete that batch.
- Use superpowers:subagent-driven-development when subagents are available; otherwise use superpowers:executing-plans.
- Do not run approval-gated runtime commands.
- Save or update execution-report.md using docs/superpowers/templates/execution-report.md.
- Set COMPONENT_STATUS=CONTINUE when verification/review is still needed, DONE when the full accepted task is implemented and verified, NEEDS_APPROVAL for gated runtime work, or BLOCKED for unclear scope.
PROMPT
      ;;
    verify)
      cat >> "$prompt_file" <<'PROMPT'
- Use superpowers:verification-before-completion.
- Run the manifest and accepted-plan validations that are safe without extra approval.
- Run the accepted smoke test if it is safe without extra approval.
- If the required smoke test needs live-stack or runtime approval, do not run it; update execution-report.md and set COMPONENT_STATUS=NEEDS_APPROVAL.
- Update execution-report.md with command evidence.
- Set COMPONENT_STATUS=CONTINUE if review is still needed, DONE if no review/fixes remain, or FAILED if validation fails.
PROMPT
      ;;
    review)
      cat >> "$prompt_file" <<'PROMPT'
- Review the current component branch against the accepted implementation plan.
- Check spec compliance before code/docs quality.
- Fix only small issues that are clearly within the accepted plan; otherwise report findings.
- Update execution-report.md review results.
- Set COMPONENT_STATUS=DONE if the branch is ready for human merge review, CONTINUE if another implementation iteration is needed, or BLOCKED/FAILED for unresolved issues.
PROMPT
      ;;
    handoff)
      cat >> "$prompt_file" <<'PROMPT'
- Write iteration-handoff.md using docs/superpowers/templates/iteration-handoff.md.
- Include audit, plan, evaluation, execution-report paths, branch name, current status, open questions, approval needs, and recommended next action.
- Do not implement changes.
- Preserve the current COMPONENT_STATUS unless the handoff reveals a new blocker.
PROMPT
      ;;
    *)
      die "unsupported phase: $phase"
      ;;
  esac
}

run_codex_prompt_file() {
  local worktree="$1"
  local component="$2"
  local phase_label="$3"
  local prompt_file="$4"
  local log_file="$5"
  local -a codex_args
  local status

  codex_args=(-a "$APPROVAL_POLICY")
  if [[ -n "$MODEL" ]]; then
    codex_args+=(-m "$MODEL")
  fi
  codex_args+=(exec -C "$worktree" -s "$SANDBOX")
  if [[ "$EPHEMERAL" -eq 1 ]]; then
    codex_args+=(--ephemeral)
  fi
  codex_args+=(-)

  info "Starting Codex: component=$component phase=$phase_label timeout=${PHASE_TIMEOUT_SECONDS}s"
  set +e
  if [[ "$STREAM_LOGS" -eq 1 ]]; then
    timeout "${PHASE_TIMEOUT_SECONDS}s" "$CODEX_BIN" "${codex_args[@]}" < "$prompt_file" 2>&1 | tee "$log_file"
    status="${PIPESTATUS[0]}"
  else
    timeout "${PHASE_TIMEOUT_SECONDS}s" "$CODEX_BIN" "${codex_args[@]}" < "$prompt_file" > "$log_file" 2>&1
    status="$?"
  fi
  set -e

  if [[ "$STREAM_LOGS" -eq 0 ]]; then
    info "Codex log tail: $log_file"
    tail -n "$LOG_TAIL_LINES" "$log_file" |
      awk '{ if (length($0) > 500) print substr($0, 1, 500) "... [line truncated]"; else print }' ||
      true
  fi

  if [[ "$status" -ne 0 ]]; then
    printf 'Codex phase failed: component=%s phase=%s status=%s log=%s\n' \
      "$component" "$phase_label" "$status" "$log_file" >&2
    return "$status"
  fi

  local log_bytes
  log_bytes="$(wc -c < "$log_file" | tr -d ' ')"
  if (( log_bytes > MAX_LOG_BYTES )); then
    printf 'Codex phase log exceeded cap: component=%s phase=%s bytes=%s cap=%s log=%s\n' \
      "$component" "$phase_label" "$log_bytes" "$MAX_LOG_BYTES" "$log_file" >&2
    return 1
  fi
}

run_codex_phase() {
  local worktree="$1"
  local component="$2"
  local title="$3"
  local mode="$4"
  local risk="$5"
  local branch="$6"
  local prompt_rel="$7"
  local phase="$8"
  local iteration="$9"
  local artifact_rel
  local context_rel
  local log_dir
  local prompt_file
  local log_file

  artifact_rel="$(component_artifact_rel "$component")"
  context_rel="$artifact_rel/context/${phase}-${iteration}.md"
  log_dir="$LOG_ROOT/$RUN_ID/$component"
  prompt_file="$log_dir/${phase}-${iteration}.prompt.txt"
  log_file="$log_dir/${phase}-${iteration}.log"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "DRY RUN: would run $component phase=$phase iteration=$iteration branch=$branch worktree=$worktree"
    return 0
  fi

  mkdir -p "$log_dir"
  mkdir -p "$worktree/$artifact_rel"
  generate_context_pack "$worktree" "$component" "$phase" "$phase" "$prompt_rel" "$context_rel"
  write_phase_prompt "$prompt_file" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" "$phase" "$iteration" "$artifact_rel" "$context_rel"

  run_codex_prompt_file "$worktree" "$component" "${phase}-${iteration}" "$prompt_file" "$log_file"
}

status_after_phase() {
  local worktree="$1"
  local component="$2"
  local state_file
  local status

  state_file="$(state_file_path "$worktree" "$component")"
  if [[ ! -f "$state_file" ]]; then
    printf 'BLOCKED\n'
    return 0
  fi

  status="$(read_state_value "$state_file" COMPONENT_STATUS || true)"
  case "$status" in
    CONTINUE|DONE|BLOCKED|NEEDS_APPROVAL|FAILED) ;;
    *) status="BLOCKED" ;;
  esac
  printf '%s\n' "$status"
}

component_uses_segmented_audit() {
  local component="$1"
  [[ "$AUDIT_MODE" == "segmented" && "$component" == "system-inventory" ]]
}

system_inventory_segments() {
  printf '%s\n' \
    "compose-services|Compose Services" \
    "routes-access|Routes And Access" \
    "env-contract|Environment Contract" \
    "tests-ci|Tests And CI" \
    "docs-drift|Documentation Drift" \
    "apps-workers|Apps And Workers"
}

write_segment_audit_prompt() {
  local prompt_file="$1"
  local component="$2"
  local title="$3"
  local mode="$4"
  local risk="$5"
  local branch="$6"
  local prompt_rel="$7"
  local artifact_rel="$8"
  local segment="$9"
  local segment_title="${10}"
  local context_rel="${11}"
  local output_rel="$artifact_rel/subaudits/${segment}.md"
  local state_rel="$artifact_rel/subaudits/${segment}.state.env"

  cat > "$prompt_file" <<PROMPT
You are a fresh Codex audit worker for one small slice of the homelab refactor workflow.

Run id: $RUN_ID
Component id: $component
Component title: $title
Component mode: $mode
Component risk: $risk
Segment id: $segment
Segment title: $segment_title
Implementation branch: $branch
Component prompt: $prompt_rel
Context pack: $context_rel
Output artifact: $output_rel
Segment state file: $state_rel

Hard rules:
- This session handles only this segment.
- Read $context_rel first and treat it as the bounded context.
- Do not read generated/binary/runtime paths such as data/**, docs/images/**, config/caddy/caddy, logs, .env, or .env.*.
- Do not run broad repo-wide searches that include generated assets.
- Do not print whole file contents to stdout. Write the artifact directly.
- Do not implement changes outside the output artifact and segment state file.
- Do not perform Docker, live service, Caddy reload, DNS, Cloudflare, router, firewall, VPN, Xray, nginx, Tailscale, live media, credential, or destructive Git operations.
- Cite repo paths for major claims.
- Distinguish confirmed facts from assumptions.

Task:
Produce a focused Markdown subaudit for "$segment_title".

Use this structure:

# $segment_title Subaudit

**Component ID:** $component
**Segment ID:** $segment
**Context Pack:** $context_rel
**Date:** $(date -u +%Y-%m-%d)

## Scope

## Confirmed Facts

## Drift And Risks

## Refactor Candidates

## Handoff Notes

At the end, write $state_rel with:
SEGMENT_STATUS=DONE
LAST_PHASE=audit
NEXT_ACTION=synthesize system inventory audit
PROMPT
}

write_segment_synthesis_prompt() {
  local prompt_file="$1"
  local component="$2"
  local title="$3"
  local mode="$4"
  local risk="$5"
  local branch="$6"
  local prompt_rel="$7"
  local artifact_rel="$8"
  local context_rel="$9"
  local state_rel="$artifact_rel/runner-state.env"

  cat > "$prompt_file" <<PROMPT
You are a fresh Codex synthesis worker for the homelab agentic refactor workflow.

Run id: $RUN_ID
Component id: $component
Component title: $title
Component mode: $mode
Component risk: $risk
Implementation branch: $branch
Component prompt: $prompt_rel
Context pack: $context_rel
Subaudit directory: $artifact_rel/subaudits
Output artifact: $artifact_rel/audit-output.md
Runner state file: $state_rel

Hard rules:
- Read the context pack and all Markdown files in $artifact_rel/subaudits.
- Do not re-run the full repo audit.
- Do not read generated/binary/runtime paths such as data/**, docs/images/**, config/caddy/caddy, logs, .env, or .env.*.
- Do not print whole file contents to stdout. Write the final artifact directly.
- Do not perform implementation changes or live/runtime operations.
- If a required subaudit is missing or unclear, write COMPONENT_STATUS=BLOCKED to $state_rel.

Task:
Synthesize the subaudits into $artifact_rel/audit-output.md using docs/superpowers/templates/audit-output.md and the system inventory prompt requirements.

The final audit must include:
- Executive Summary
- Service Catalog
- Route & Access Matrix
- Environment Contract
- Test & Validation Matrix
- Drift & Risk Register
- Recommended Next Audits
- Handoff

At the end, write $state_rel with:
COMPONENT_STATUS=DONE
LAST_PHASE=audit
NEXT_ACTION=review audit-output.md and continue with handoff
PROMPT
}

run_segmented_audit() {
  local worktree="$1"
  local component="$2"
  local title="$3"
  local mode="$4"
  local risk="$5"
  local branch="$6"
  local prompt_rel="$7"
  local artifact_rel
  local log_dir
  local segment
  local segment_title
  local context_rel
  local prompt_file
  local log_file
  local state_file
  local status

  artifact_rel="$(component_artifact_rel "$component")"
  log_dir="$LOG_ROOT/$RUN_ID/$component"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "DRY RUN: would run segmented audit for $component"
    system_inventory_segments | while IFS='|' read -r segment segment_title; do
      info "DRY RUN: segment=$segment title=$segment_title"
    done
    return 0
  fi

  mkdir -p "$log_dir"
  mkdir -p "$worktree/$artifact_rel/subaudits" "$worktree/$artifact_rel/context"

  while IFS='|' read -r segment segment_title; do
    context_rel="$artifact_rel/context/audit-${segment}.md"
    prompt_file="$log_dir/audit-${segment}.prompt.txt"
    log_file="$log_dir/audit-${segment}.log"
    state_file="$worktree/$artifact_rel/subaudits/${segment}.state.env"

    generate_context_pack "$worktree" "$component" "$segment" "audit" "$prompt_rel" "$context_rel"
    write_segment_audit_prompt "$prompt_file" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" "$artifact_rel" "$segment" "$segment_title" "$context_rel"
    run_codex_prompt_file "$worktree" "$component" "audit-${segment}" "$prompt_file" "$log_file"

    status="$(read_state_value "$state_file" SEGMENT_STATUS || true)"
    [[ "$status" == "DONE" ]] || die "segment $segment did not finish with SEGMENT_STATUS=DONE"
  done < <(system_inventory_segments)

  context_rel="$artifact_rel/context/audit-synthesis.md"
  prompt_file="$log_dir/audit-synthesis.prompt.txt"
  log_file="$log_dir/audit-synthesis.log"
  generate_context_pack "$worktree" "$component" "synthesis" "audit" "$prompt_rel" "$context_rel"
  write_segment_synthesis_prompt "$prompt_file" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" "$artifact_rel" "$context_rel"
  run_codex_prompt_file "$worktree" "$component" "audit-synthesis" "$prompt_file" "$log_file"
}

component_skips_implementation() {
  local mode="$1"

  case "$mode" in
    audit-only|planning-only) return 0 ;;
    *) return 1 ;;
  esac
}

run_component() {
  local component="$1"
  local title
  local mode
  local risk
  local branch
  local prompt_rel
  local worktree
  local status
  local iteration

  title="$(manifest_value "$component" title)"
  mode="$(manifest_value "$component" mode)"
  risk="$(manifest_value "$component" risk)"
  branch="$(manifest_value "$component" implementation_branch)"
  prompt_rel="$(manifest_value "$component" prompt)"

  [[ -n "$branch" ]] || die "component missing implementation_branch: $component"
  [[ -n "$prompt_rel" ]] || die "component missing prompt path: $component"
  [[ -f "$ROOT/$prompt_rel" ]] || die "component prompt does not exist: $prompt_rel"

  worktree="$(ensure_component_worktree "$component" "$branch")"

  info "Component $component: $title"

  if phase_enabled audit; then
    if component_uses_segmented_audit "$component"; then
      run_segmented_audit "$worktree" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel"
    else
      run_codex_phase "$worktree" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" audit 1
    fi
    status="$(status_after_phase "$worktree" "$component")"
    [[ "$status" != "NEEDS_APPROVAL" && "$status" != "BLOCKED" && "$status" != "FAILED" ]] ||
      die "component $component stopped after audit with status $status"
  fi

  if component_skips_implementation "$mode"; then
    if phase_enabled handoff; then
      run_codex_phase "$worktree" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" handoff 1
    fi
    info "Component $component finished mode=$mode"
    return 0
  fi

  if phase_enabled plan; then
    run_codex_phase "$worktree" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" plan 1
    status="$(status_after_phase "$worktree" "$component")"
    [[ "$status" != "NEEDS_APPROVAL" && "$status" != "BLOCKED" && "$status" != "FAILED" ]] ||
      die "component $component stopped after plan with status $status"
  fi

  if phase_enabled evaluate; then
    run_codex_phase "$worktree" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" evaluate 1
    status="$(status_after_phase "$worktree" "$component")"
    [[ "$status" != "NEEDS_APPROVAL" && "$status" != "BLOCKED" && "$status" != "FAILED" ]] ||
      die "component $component stopped after evaluate with status $status"
  fi

  if phase_enabled implement || phase_enabled verify || phase_enabled review; then
    for ((iteration = 1; iteration <= MAX_ITERATIONS; iteration++)); do
      if phase_enabled implement; then
        run_codex_phase "$worktree" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" implement "$iteration"
        status="$(status_after_phase "$worktree" "$component")"
        case "$status" in
          DONE) break ;;
          NEEDS_APPROVAL|BLOCKED|FAILED) die "component $component stopped after implement iteration $iteration with status $status" ;;
        esac
      fi

      if phase_enabled verify; then
        run_codex_phase "$worktree" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" verify "$iteration"
        status="$(status_after_phase "$worktree" "$component")"
        case "$status" in
          DONE) break ;;
          NEEDS_APPROVAL|BLOCKED|FAILED) die "component $component stopped after verify iteration $iteration with status $status" ;;
        esac
      fi

      if phase_enabled review; then
        run_codex_phase "$worktree" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" review "$iteration"
        status="$(status_after_phase "$worktree" "$component")"
        case "$status" in
          DONE) break ;;
          NEEDS_APPROVAL|BLOCKED|FAILED) die "component $component stopped after review iteration $iteration with status $status" ;;
        esac
      fi

      status="$(status_after_phase "$worktree" "$component")"
      [[ "$status" == "DONE" ]] && break

      if [[ "$iteration" -eq "$MAX_ITERATIONS" ]]; then
        die "component $component reached max iterations without DONE status"
      fi
    done
  fi

  if phase_enabled handoff; then
    run_codex_phase "$worktree" "$component" "$title" "$mode" "$risk" "$branch" "$prompt_rel" handoff 1
  fi

  info "Component $component complete or handed off"
}

print_plan() {
  local component
  local branch
  local mode
  local prompt_rel

  printf 'Run id: %s\n' "$RUN_ID"
  printf 'Base branch: %s\n' "$BASE_BRANCH"
  printf 'Worktree root: %s\n' "$WORKTREE_ROOT"
  printf 'Log root: %s\n' "$LOG_ROOT"
  printf 'Phases: %s\n' "$PHASES"
  printf 'Max iterations: %s\n' "$MAX_ITERATIONS"
  printf 'Audit mode: %s\n' "$AUDIT_MODE"
  printf 'Phase timeout seconds: %s\n' "$PHASE_TIMEOUT_SECONDS"
  printf 'Max log bytes: %s\n' "$MAX_LOG_BYTES"
  printf 'Stream logs: %s\n' "$([[ "$STREAM_LOGS" -eq 1 ]] && printf yes || printf no)"
  printf 'Codex: %s\n' "$CODEX_BIN"
  printf 'Sandbox: %s\n' "$SANDBOX"
  printf 'Approval policy: %s\n' "$APPROVAL_POLICY"
  printf 'Sessions: %s\n' "$([[ "$EPHEMERAL" -eq 1 ]] && printf ephemeral || printf persisted)"
  printf '\nComponents:\n'

  for component in "${SELECTED_COMPONENTS[@]}"; do
    branch="$(manifest_value "$component" implementation_branch)"
    mode="$(manifest_value "$component" mode)"
    prompt_rel="$(manifest_value "$component" prompt)"
    printf '  - %s branch=%s mode=%s prompt=%s\n' "$component" "$branch" "$mode" "$prompt_rel"
    if component_uses_segmented_audit "$component"; then
      system_inventory_segments | sed 's/^/      segment: /'
    fi
  done
}

print_plan

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf '\nDry run only; no worktrees or Codex sessions started.\n'
  for component in "${SELECTED_COMPONENTS[@]}"; do
    branch="$(manifest_value "$component" implementation_branch)"
    ensure_component_worktree "$component" "$branch" >/dev/null
  done
  exit 0
fi

if [[ "$YES" -eq 0 ]]; then
  if [[ -t 0 ]]; then
    printf '\nThis will create/reuse component worktrees and launch Codex sessions. Continue? [y/N] '
    read -r answer
    case "$answer" in
      y|Y|yes|YES) ;;
      *) die "aborted" ;;
    esac
  else
    die "non-interactive run requires --yes"
  fi
fi

for component in "${SELECTED_COMPONENTS[@]}"; do
  run_component "$component"
done

info "All selected components processed. Run id: $RUN_ID"
