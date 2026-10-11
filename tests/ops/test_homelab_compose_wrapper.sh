#!/usr/bin/env bash
# Regression test: homelab-compose.py must resolve REPO_ROOT to the repo root
# (the directory containing docker-compose.yml), not scripts/. An off-by-one
# made it look for scripts/docker-compose.yml and every invocation failed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

RESULT="$(python3 - "$ROOT" <<'PYEOF'
import importlib.util
import sys
from pathlib import Path

expected_root = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location(
    "homelab_compose_wrapper_test", expected_root / "scripts" / "ops" / "homelab-compose.py"
)
module = importlib.util.module_from_spec(spec)
# Executes the sys.path.insert + `from infisical_fetch import ...` lines —
# a broken lib path fails here too.
spec.loader.exec_module(module)

errors = []
if module.REPO_ROOT != expected_root:
    errors.append(f"REPO_ROOT={module.REPO_ROOT} but expected {expected_root}")
if not (module.REPO_ROOT / "docker-compose.yml").is_file():
    errors.append("docker-compose.yml not found under REPO_ROOT")
if (module.REPO_ROOT / "scripts" / "docker-compose.yml").exists():
    errors.append("REPO_ROOT resolves inside scripts/ (off-by-one regression)")

captured = {}

def fake_fetch(paths, required_keys=()):
    captured["paths"] = paths
    captured["required_keys"] = tuple(required_keys)
    return {key: f"value-{key}" for key in required_keys}

def fake_exec(file, argv):
    captured["file"] = file
    captured["argv"] = argv
    return 0

module.fetch_secrets = fake_fetch
module.os.execvp = fake_exec
original_argv = sys.argv
try:
    sys.argv = [str(module.__file__), "--env-file", ".e""nv.example", "config", "--quiet"]
    module.main()
finally:
    sys.argv = original_argv

if captured.get("file") != "docker":
    errors.append("config --quiet did not execute docker")
if captured.get("argv", [])[-2:] != ["config", "--quiet"]:
    errors.append(f"config --quiet argv not preserved: {captured.get('argv')}")
expected_required_keys = module.managed_secret_keys_for_paths(
    module.split_csv(module.COMPOSE_PATHS)
)
if not expected_required_keys:
    errors.append("managed Compose catalog must not be empty")
if captured.get("paths") != module.COMPOSE_PATHS:
    errors.append(
        f"config --quiet paths={captured.get('paths')} but expected {module.COMPOSE_PATHS}"
    )
if captured.get("required_keys") != expected_required_keys:
    errors.append(
        "config --quiet required_keys did not match the managed Compose catalog"
    )

if errors:
    print("; ".join(errors))
    sys.exit(1)
print("OK")
PYEOF
)" || fail "homelab-compose.py REPO_ROOT regression: ${RESULT}"

pass "homelab-compose.py resolves ${ROOT}/docker-compose.yml (not ${ROOT}/scripts/)"
