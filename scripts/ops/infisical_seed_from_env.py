#!/usr/bin/env python3
"""Upsert managed secrets from a local .env into Infisical (stdlib REST only).

Never print secret values. Never persist client secrets to disk.

Usage:
    infisical_seed_from_env.py [--apply] [--clear-env] [--env-file PATH]
                               [--project-id ID] [--env SLUG]

Default: dry-run (list nonempty managed keys; names only).
--apply requires INFISICAL_SEED_CLIENT_ID and INFISICAL_SEED_CLIENT_SECRET
   (or an existing INFISICAL_TOKEN) in the environment.
--clear-env verifies Infisical via read bootstrap before blanking managed
   values in the env file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from infisical_fetch import (
    InfisicalClient,
    bootstrap_path,
    load_bootstrap,
    validate_bootstrap_perms,
)
from managed_secrets import MANAGED_SECRET_KEY_PATH_PAIRS, managed_secret_keys

RUNTIME_ROOT = Path(os.environ.get("HOMELAB_ROOT", Path.home() / "homelab"))

MANAGED_SECRET_KEYS = frozenset(managed_secret_keys())


def _trim_whitespace(s: str) -> str:
    return s.strip()


def _strip_quotes(s: str) -> str:
    if len(s) >= 2:
        if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
            return s[1:-1]
    return s


def _value_nonempty(val: str) -> bool:
    return bool(_trim_whitespace(_strip_quotes(_trim_whitespace(val))))


def _parse_env_value(key: str, file_path: Path) -> str:
    if not file_path.is_file():
        return ""
    for line in file_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" in stripped:
            k, _, v = stripped.partition("=")
            if k.strip() == key:
                return v
    return ""


def _require_runtime_gate(env_file: Path) -> None:
    if os.environ.get("HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME"):
        return
    resolved = env_file.resolve()
    env_dir = resolved.parent
    if REPO_ROOT == RUNTIME_ROOT or env_dir == RUNTIME_ROOT:
        return
    raise SystemExit(
        f"FAIL: --apply/--clear-env only allowed from runtime checkout "
        f"{RUNTIME_ROOT} (env-file under that tree). "
        f"Set HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME=1 for tests only."
    )


def _resolve_token() -> str | None:
    token = os.environ.get("INFISICAL_TOKEN")
    if token:
        return token
    service_token = os.environ.get("INFISICAL_SERVICE_TOKEN")
    if service_token:
        os.environ["INFISICAL_TOKEN"] = service_token
        return service_token
    env_file = os.environ.get("ENV_FILE", "")
    if env_file:
        for key in ("INFISICAL_TOKEN", "INFISICAL_SERVICE_TOKEN"):
            val = _parse_env_value(key, Path(env_file))
            if _value_nonempty(val):
                os.environ["INFISICAL_TOKEN"] = val
                return val
    return None


def _ensure_project_id(env_file: Path, project_id_arg: str | None) -> str | None:
    if project_id_arg:
        os.environ["INFISICAL_PROJECT_ID"] = project_id_arg
        return project_id_arg
    env_pid = os.environ.get("INFISICAL_PROJECT_ID")
    if env_pid:
        return env_pid
    val = _parse_env_value("INFISICAL_PROJECT_ID", env_file)
    if _value_nonempty(val):
        os.environ["INFISICAL_PROJECT_ID"] = val
        return val
    try:
        creds = load_bootstrap()
        pid = creds.get("INFISICAL_PROJECT_ID")
        if pid:
            os.environ["INFISICAL_PROJECT_ID"] = pid
            env_slug = creds.get("INFISICAL_ENV", os.environ.get("INFISICAL_ENV_SLUG", "prod"))
            os.environ["INFISICAL_ENV_SLUG"] = env_slug
            return pid
    except SystemExit:
        pass
    return None


def _list_seedable(env_file: Path) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for path, key in MANAGED_SECRET_KEY_PATH_PAIRS:
        val = _parse_env_value(key, env_file)
        if _value_nonempty(val):
            result.append((key, path))
    return result


def dry_run(env_file: Path) -> None:
    seedable = _list_seedable(env_file)
    print(f"Dry-run: nonempty managed keys in {env_file} (names only):")
    for key, path in seedable:
        print(f"  seedable: {key} ({path})")
    if not seedable:
        print("  (none)")
    else:
        print(f"Total: {len(seedable)}")


def _make_client_from_env() -> InfisicalClient:
    bootstrap = load_bootstrap()
    host = bootstrap.get("INFISICAL_API_URL", "https://app.infisical.com")
    existing_token = os.environ.get("INFISICAL_TOKEN")
    if existing_token:
        return InfisicalClient.from_token(existing_token, host=host)
    return InfisicalClient.from_universal_auth(
        bootstrap["INFISICAL_CLIENT_ID"],
        bootstrap["INFISICAL_CLIENT_SECRET"],
        host=host,
    )


def do_apply(env_file: Path, env_slug: str, project_id: str | None) -> None:
    token = _resolve_token()
    bootstrap = load_bootstrap()
    host = bootstrap.get("INFISICAL_API_URL", "https://app.infisical.com")

    if not token:
        client_id = os.environ.get("INFISICAL_SEED_CLIENT_ID")
        client_secret = os.environ.get("INFISICAL_SEED_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise SystemExit(
                "FAIL: --apply requires INFISICAL_TOKEN (or INFISICAL_SERVICE_TOKEN), "
                "or INFISICAL_SEED_CLIENT_ID and INFISICAL_SEED_CLIENT_SECRET in the environment"
            )
        _ensure_project_id(env_file, project_id)
        client = InfisicalClient.from_universal_auth(client_id, client_secret, host=host)
        os.environ.pop("INFISICAL_SEED_CLIENT_SECRET", None)
    else:
        _ensure_project_id(env_file, project_id)
        client = InfisicalClient.from_token(token, host=host)

    pid = project_id or os.environ.get("INFISICAL_PROJECT_ID")
    if not pid:
        raise SystemExit(
            "FAIL: --apply requires --project-id <ID> "
            "or INFISICAL_PROJECT_ID in the environment/.env"
        )

    applied = 0
    for key, path in _list_seedable(env_file):
        val = _parse_env_value(key, env_file)
        if not _value_nonempty(val):
            print(f"WARN: skip empty {key}", file=sys.stderr)
            continue
        try:
            existing = client.get_secret(
                secret_name=key,
                project_id=pid,
                environment=env_slug,
                secret_path=path,
            )
            if existing:
                client.update_secret(
                    secret_name=key,
                    secret_value=_strip_quotes(_trim_whitespace(val)),
                    project_id=pid,
                    environment=env_slug,
                    secret_path=path,
                )
                print(f"OK: updated {key} -> {path}")
            else:
                client.create_secret(
                    secret_name=key,
                    secret_value=_strip_quotes(_trim_whitespace(val)),
                    project_id=pid,
                    environment=env_slug,
                    secret_path=path,
                )
                print(f"OK: created {key} -> {path}")
        except Exception as exc:
            print(f"WARN: failed to upsert {key}: {exc}", file=sys.stderr)
            continue
        applied += 1

    print(f"OK: applied {applied} secret(s)")


def _verify_infisical_has_nonempty_local(env_file: Path, env_slug: str, project_id: str | None) -> None:
    bootstrap = load_bootstrap()
    host = bootstrap.get("INFISICAL_API_URL", "https://app.infisical.com")

    token = _resolve_token()
    if not token:
        try:
            client_id = bootstrap["INFISICAL_CLIENT_ID"]
            client_secret = bootstrap["INFISICAL_CLIENT_SECRET"]
            client = InfisicalClient.from_universal_auth(client_id, client_secret, host=host)
            os.environ["INFISICAL_TOKEN"] = client.token or ""
            os.environ.pop("INFISICAL_CLIENT_SECRET", None)
            os.environ.pop("INFISICAL_CLIENT_ID", None)
        except SystemExit:
            return
    else:
        client = InfisicalClient.from_token(token, host=host)

    pid = project_id or os.environ.get("INFISICAL_PROJECT_ID") or bootstrap.get("INFISICAL_PROJECT_ID")
    if not pid:
        raise SystemExit("FAIL: no project ID available for verification")

    for key, path in MANAGED_SECRET_KEY_PATH_PAIRS:
        val = _parse_env_value(key, env_file)
        if not _value_nonempty(val):
            continue
        existing = client.get_secret(
            secret_name=key,
            project_id=pid,
            environment=env_slug,
            secret_path=path,
        )
        if not existing:
            raise SystemExit(
                f"FAIL: Infisical missing or unreadable {key} at {path}; refusing --clear-env"
            )


def _blank_managed_in_env_file(env_file: Path) -> None:
    tmp = tempfile.mktemp(dir=env_file.parent)
    managed = MANAGED_SECRET_KEYS
    with open(tmp, "w", encoding="utf-8") as out:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw
            stripped = line.strip()
            prefix = ""
            if stripped.startswith("export "):
                prefix = "export "
                stripped = stripped[7:].strip()
            if "=" in stripped:
                key, _, _ = stripped.partition("=")
                if key in managed:
                    out.write(f"{prefix}{key}=\n")
                    continue
            out.write(line + "\n")
    shutil.move(tmp, str(env_file))


def do_clear_env(env_file: Path, env_slug: str, project_id: str | None) -> None:
    _verify_infisical_has_nonempty_local(env_file, env_slug, project_id)
    _blank_managed_in_env_file(env_file)
    print(f"OK: blanked managed secret values in {env_file}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upsert managed secrets from local .env into Infisical"
    )
    parser.add_argument("--apply", action="store_true", help="Apply changes to Infisical")
    parser.add_argument("--clear-env", action="store_true", help="Blank managed values in env file")
    parser.add_argument(
        "--env-file",
        default=str(RUNTIME_ROOT / ".env"),
        help="Path to .env file (default: %(default)s)",
    )
    parser.add_argument("--project-id", help="Infisical project ID")
    parser.add_argument("--env", default="prod", help="Infisical environment slug (default: prod)")
    args = parser.parse_args()

    env_file = Path(args.env_file)
    env_slug = args.env
    project_id = args.project_id

    if not env_file.is_file():
        raise SystemExit(f"FAIL: env file not found: {env_file}")

    if not args.apply and not args.clear_env:
        dry_run(env_file)
        return

    _require_runtime_gate(env_file)

    if args.apply:
        do_apply(env_file, env_slug, project_id)

    if args.clear_env:
        do_clear_env(env_file, env_slug, project_id)


if __name__ == "__main__":
    main()
