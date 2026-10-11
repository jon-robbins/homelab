#!/usr/bin/env python3
"""Infisical secret fetcher — stdlib-only, zero dependencies.

Provides:
  1. Importable API for Python tools:
     from infisical_fetch import fetch_secrets, ensure_secrets, InfisicalClient

  2. CLI:
     fetch   Fetch secrets for given paths then exec a command.
     require Check if required keys are resolvable; if not, fetch and re-exec.

Bootstrap credentials: ~/.config/homelab/infisical.env (0400, owner-only).
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import json
import os
import re
import stat
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from managed_secrets import managed_secret_keys_for_paths

BOOTSTRAP_DEFAULT = Path.home() / ".config" / "homelab" / "infisical.env"

# Secret name validation pattern
SECRET_NAME_PATTERN = re.compile(r'^[A-Z_][A-Z0-9_]*$')

SENSITIVE_ENV_KEYS: frozenset[str] = frozenset({
    "INFISICAL_CLIENT_ID",
    "INFISICAL_CLIENT_SECRET",
    "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID",
    "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET",
    "INFISICAL_TOKEN",
    "INFISICAL_DISABLE_UPDATE_CHECK",
})


class InfisicalClient:
    """Minimal Infisical REST API client (stdlib urllib only)."""

    def __init__(
        self,
        host: str = "https://app.infisical.com",
        token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        org_slug: str | None = None,
    ):
        self.host = host.rstrip("/")
        self.token = token
        self.client_id = client_id
        self.client_secret = client_secret
        self.org_slug = org_slug
        self._token_refreshed = False  # Prevent infinite refresh loops

    @classmethod
    def from_universal_auth(
        cls,
        client_id: str,
        client_secret: str,
        host: str = "https://app.infisical.com",
        org_slug: str | None = None,
    ) -> "InfisicalClient":
        url = f"{host.rstrip('/')}/api/v1/auth/universal-auth/login"
        payload: dict[str, Any] = {"clientId": client_id, "clientSecret": client_secret}
        if org_slug:
            payload["organizationSlug"] = org_slug
        resp_data = _post_json(url, payload)
        token = resp_data.get("accessToken")
        if not token:
            raise RuntimeError(f"login missing accessToken: {resp_data}")
        return cls(
            host=host,
            token=token,
            client_id=client_id,
            client_secret=client_secret,
            org_slug=org_slug,
        )

    @classmethod
    def from_token(cls, token: str, host: str = "https://app.infisical.com") -> "InfisicalClient":
        return cls(host=host, token=token)

    def _refresh_token(self) -> None:
        """Refresh the access token using stored credentials."""
        if not self.client_id or not self.client_secret:
            raise RuntimeError("Cannot refresh token: no credentials available")
        
        url = f"{self.host}/api/v1/auth/universal-auth/login"
        payload: dict[str, Any] = {"clientId": self.client_id, "clientSecret": self.client_secret}
        if self.org_slug:
            payload["organizationSlug"] = self.org_slug
        resp_data = _post_json(url, payload)
        new_token = resp_data.get("accessToken")
        if not new_token:
            raise RuntimeError("Token refresh failed: missing accessToken")
        self.token = new_token
        self._token_refreshed = True

    def _request_with_refresh(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make an API request with automatic token refresh on 401."""
        try:
            if method == "GET":
                return _get_json(url, self.token)
            elif method == "POST":
                return _post_json(url, payload or {}, self.token)
            elif method == "PATCH":
                return _patch_json(url, payload or {}, self.token)
            else:
                raise ValueError(f"Unsupported method: {method}")
        except RuntimeError as e:
            # Check if this is a 401 error and we can refresh the token
            if "HTTP 401" in str(e) and self.client_id and self.client_secret and not self._token_refreshed:
                # Refresh token and retry once
                self._refresh_token()
                if method == "GET":
                    return _get_json(url, self.token)
                elif method == "POST":
                    return _post_json(url, payload or {}, self.token)
                elif method == "PATCH":
                    return _patch_json(url, payload or {}, self.token)
            raise

    def list_secrets(
        self,
        project_id: str,
        environment: str = "prod",
        secret_path: str = "/",
        view_secret_value: bool = True,
        recursive: bool = False,
    ) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({
            "projectId": project_id,
            "environment": environment,
            "secretPath": secret_path,
            "viewSecretValue": str(view_secret_value).lower(),
            "recursive": str(recursive).lower(),
        })
        url = f"{self.host}/api/v4/secrets?{params}"
        data = self._request_with_refresh("GET", url)
        return data.get("secrets", [])

    def get_secret(
        self,
        secret_name: str,
        project_id: str,
        environment: str = "prod",
        secret_path: str = "/",
    ) -> dict[str, Any] | None:
        secrets = self.list_secrets(project_id, environment, secret_path)
        for s in secrets:
            if s.get("secretKey") == secret_name:
                return s
        return None

    def create_secret(
        self,
        secret_name: str,
        secret_value: str,
        project_id: str,
        environment: str = "prod",
        secret_path: str = "/",
    ) -> dict[str, Any]:
        url = f"{self.host}/api/v4/secrets/{urllib.parse.quote(secret_name, safe='')}"
        payload: dict[str, Any] = {
            "projectId": project_id,
            "environment": environment,
            "secretValue": secret_value,
            "secretPath": secret_path,
        }
        data = self._request_with_refresh("POST", url, payload)
        return data.get("secret", data)

    def update_secret(
        self,
        secret_name: str,
        secret_value: str,
        project_id: str,
        environment: str = "prod",
        secret_path: str = "/",
    ) -> dict[str, Any]:
        url = f"{self.host}/api/v4/secrets/{urllib.parse.quote(secret_name, safe='')}"
        payload: dict[str, Any] = {
            "projectId": project_id,
            "environment": environment,
            "secretValue": secret_value,
            "secretPath": secret_path,
        }
        data = self._request_with_refresh("PATCH", url, payload)
        return data.get("secret", data)


def _validate_secret_name(name: str) -> None:
    """Validate secret name matches allowed pattern."""
    if not SECRET_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid secret name format: {name}")


def _create_tls_context() -> ssl.SSLContext:
    """Create a TLS context with normal CA verification and TLS 1.2+."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _http_request(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    if os.environ.get("HOMELAB_INFISICAL_TEST_MOCK") == "1":
        if os.environ.get("HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME") != "1":
            raise RuntimeError(
                "Infisical test mock requires HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME=1"
            )
        return _http_request_mock(url, method, body, token)

    data = json.dumps(body).encode() if body else None
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        # Explicit UA: eu.infisical.com's WAF rejects urllib's default
        # "Python-urllib/x.y" User-Agent with HTTP 403.
        "User-Agent": "homelab-infisical-fetch/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    if url.startswith("https://"):
        ssl_context = _create_tls_context()
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl_context)
        )
    else:
        opener = urllib.request.build_opener()
    
    # Retry logic with exponential backoff for transient failures
    last_exception = None
    for attempt in range(max_retries):
        try:
            # Create a new Request object for each attempt (Request objects can only be used once)
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with opener.open(req, timeout=30) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # Don't retry client errors (4xx) except 429 (rate limit)
            if exc.code < 500 and exc.code != 429:
                # Sanitize error messages to avoid leaking sensitive data
                raise RuntimeError(f"HTTP {exc.code} {method}: request failed") from exc
            last_exception = exc
        except urllib.error.URLError as exc:
            # Network errors are retryable
            last_exception = exc
        except Exception as exc:
            # Other exceptions are not retryable
            raise RuntimeError(f"Request failed: {type(exc).__name__}") from exc
        
        # Wait before retrying (exponential backoff)
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            time.sleep(wait_time)
    
    # All retries exhausted
    if isinstance(last_exception, urllib.error.HTTPError):
        raise RuntimeError(f"HTTP {last_exception.code} {method}: request failed after {max_retries} attempts") from last_exception
    else:
        raise RuntimeError(f"Network error: request failed after {max_retries} attempts") from last_exception


def _http_request_mock(
    url: str,
    method: str,
    body: dict[str, Any] | None,
    token: str | None,
) -> dict[str, Any]:
    """Mock HTTP request handler for testing only.

    Requires HOMELAB_INFISICAL_TEST_MOCK=1 and the runtime escape hatch.
    """
    mock_log = os.environ.get("INFISICAL_MOCK_LOG", "")
    
    # Validate mock log path to prevent arbitrary file writes
    if mock_log:
        try:
            log_path = Path(mock_log).resolve()
            # Only allow writes to /tmp or test directories
            allowed_prefixes = ["/tmp", "/var/tmp", str(Path.home() / "homelab/tests")]
            if not any(str(log_path).startswith(prefix) for prefix in allowed_prefixes):
                raise ValueError(f"Mock log path not in allowed directories: {log_path}")
        except (ValueError, OSError) as e:
            raise RuntimeError(f"Invalid mock log path: {e}") from e
    
    log_entry = f"{method}:{url}"
    if body:
        key = body.get("secretKey") or body.get("secret_name") or ""
        if key:
            log_entry += f":{key}"
    
    if mock_log:
        with open(mock_log, "a") as f:
            f.write(log_entry + "\n")

    if "/auth/universal-auth/login" in url:
        return {"accessToken": "mock-token"}
    if "/api/v4/secrets" in url:
        if method == "GET":
            existing = os.environ.get("INFISICAL_MOCK_EXISTING", "")
            if existing:
                secrets = []
                for name in existing.split(","):
                    name = name.strip()
                    if name:
                        secrets.append({"secretKey": name, "secretValue": "mock-value", "secretPath": "/"})
                return {"secrets": secrets}
            return {"secrets": []}
        if method == "POST":
            return {"secret": {"secretKey": body.get("secretKey", "")}}
        if method == "PATCH":
            return {"secret": {"secretKey": body.get("secretKey", "")}}
    return {}


def _get_json(url: str, token: str | None = None) -> dict[str, Any]:
    return _http_request(url, "GET", token=token)


def _post_json(url: str, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    return _http_request(url, "POST", body=body, token=token)


def _patch_json(url: str, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    return _http_request(url, "PATCH", body=body, token=token)


def bootstrap_path() -> Path:
    return Path(os.environ.get("HOMELAB_INFISICAL_BOOTSTRAP", str(BOOTSTRAP_DEFAULT)))


def validate_bootstrap_perms(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"FAIL: bootstrap file not found: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SystemExit(f"FAIL: bootstrap file must not be group/other accessible: {path}")
    if path.stat().st_uid != os.geteuid():
        raise SystemExit(
            f"FAIL: bootstrap file owner uid ({path.stat().st_uid}) "
            f"must match invoking user ({os.geteuid()}): {path}"
        )


def load_bootstrap(path: Path | None = None) -> dict[str, str]:
    if path is None:
        path = bootstrap_path()
    validate_bootstrap_perms(path)
    creds: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("INFISICAL_") and "=" in line:
            key, _, val = line.partition("=")
            creds[key] = val
    for required in ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID"):
        if not creds.get(required):
            raise SystemExit(f"FAIL: bootstrap missing required field: {required}")
    creds.setdefault("INFISICAL_ENV", "prod")
    return creds


def split_csv(csv: str) -> list[str]:
    return [item.strip() for item in csv.split(",") if item.strip()]


def _paths_cover(required_csv: str, loaded_csv: str | None = None) -> bool:
    if loaded_csv is None:
        loaded_csv = os.environ.get("HOMELAB_INFISICAL_PATHS", "")
    if not loaded_csv:
        return False
    required = split_csv(required_csv)
    loaded = split_csv(loaded_csv)
    return all(req in loaded for req in required)


def _paths_union(a_csv: str, b_csv: str) -> str:
    result: list[str] = []
    for item in split_csv(a_csv) + split_csv(b_csv):
        if item not in result:
            result.append(item)
    return ",".join(result)


def secret_resolvable(name: str) -> tuple[bool, str | None]:
    """Check if a secret is resolvable from environment or NAME_FILE.
    
    Returns:
        Tuple of (is_resolvable, value_or_none)
    """
    # Validate secret name format
    _validate_secret_name(name)
    
    val = os.environ.get(name)
    if val:
        return True, val
    
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        try:
            path = Path(file_path)
            if path.is_file() and os.access(path, os.R_OK):
                val = path.read_text(encoding="utf-8").rstrip("\r\n")
                if val:
                    os.environ[name] = val
                    return True, val
        except OSError:
            pass
    
    return False, None


def _resolve_client(bootstrap: dict[str, str]) -> InfisicalClient:
    host = bootstrap.get("INFISICAL_API_URL", "https://app.infisical.com")
    existing_token = os.environ.get("INFISICAL_TOKEN")
    if existing_token:
        return InfisicalClient.from_token(existing_token, host=host)
    return InfisicalClient.from_universal_auth(
        bootstrap["INFISICAL_CLIENT_ID"],
        bootstrap["INFISICAL_CLIENT_SECRET"],
        host=host,
    )


def fetch_secrets(
    paths_csv: str,
    bootstrap_path: Path | None = None,
    bootstrap: dict[str, str] | None = None,
    required_keys: Iterable[str] = (),
) -> dict[str, str]:
    """Fetch secrets for given Infisical paths.

    Args:
        paths_csv: Comma-separated Infisical paths (e.g. "/network,/qbt")
        bootstrap_path: Path to bootstrap env file (optional if bootstrap provided)
        bootstrap: Pre-loaded bootstrap credentials (optional)
        required_keys: Secret names that must be present and non-empty.

    Returns:
        Dict mapping secret key names to their values.
    """
    paths = split_csv(paths_csv)
    try:
        allowed_by_path = {
            path: set(managed_secret_keys_for_paths((path,))) for path in paths
        }
    except ValueError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc

    allowed_keys = set().union(*allowed_by_path.values()) if allowed_by_path else set()

    required = tuple(dict.fromkeys(required_keys))
    outside_scope = sorted(set(required) - allowed_keys)
    if outside_scope:
        raise SystemExit(
            "FAIL: required secret(s) not in requested Infisical paths: "
            + ", ".join(outside_scope)
        )

    if bootstrap is None:
        bootstrap = load_bootstrap(bootstrap_path)
    client = _resolve_client(bootstrap)
    env_slug = bootstrap["INFISICAL_ENV"]
    project_id = bootstrap["INFISICAL_PROJECT_ID"]

    env_vars: dict[str, str] = {}
    for path in paths:
        try:
            secrets = client.list_secrets(
                project_id=project_id,
                environment=env_slug,
                secret_path=path,
                view_secret_value=True,
            )
        except Exception as exc:
            raise SystemExit(
                f"FAIL: Infisical secret fetch failed for path {path}: {exc}"
            ) from exc
        for secret in secrets:
            key = secret.get("secretKey")
            value = secret.get("secretValue")
            if key not in allowed_by_path[path] or value is None:
                continue
            if key in env_vars:
                raise SystemExit(f"FAIL: duplicate managed secret returned: {key}")
            env_vars[key] = value

    missing = sorted(key for key in required if not env_vars.get(key))
    if missing:
        raise SystemExit("FAIL: missing required secret(s): " + ", ".join(missing))
    return env_vars


def ensure_secrets(paths_csv: str, keys_csv: str) -> dict[str, str]:
    """Ensure required keys are available in environment.

    If all keys are already resolvable (env or NAME_FILE), returns immediately.
    Otherwise, fetches secrets from Infisical and sets them in os.environ.

    Args:
        paths_csv: Comma-separated Infisical paths to fetch from
        keys_csv: Comma-separated required key names

    Returns:
        Dict of fetched secrets (empty if all keys were already resolvable).
    """
    keys = tuple(split_csv(keys_csv))
    missing: list[str] = []
    for k in keys:
        resolvable, _ = secret_resolvable(k)
        if not resolvable:
            missing.append(k)

    if not missing:
        return {}

    if _paths_cover(paths_csv):
        raise SystemExit(f"FAIL: missing required secret(s): {', '.join(missing)}")

    bootstrap = load_bootstrap()
    env_vars = fetch_secrets(paths_csv, bootstrap=bootstrap, required_keys=keys)

    for key, val in env_vars.items():
        os.environ[key] = val

    os.environ["HOMELAB_INFISICAL_PATHS"] = paths_csv
    os.environ["HOMELAB_INFISICAL_LOADED"] = "1"

    _scrub_infisical_env()
    return env_vars


def _scrub_infisical_env() -> None:
    for key in SENSITIVE_ENV_KEYS:
        os.environ.pop(key, None)


def clear_secrets(secrets: dict[str, str]) -> None:
    """Clear secret values from memory.
    
    Args:
        secrets: Dictionary of secrets to clear
    """
    for key in secrets:
        if key in secrets:
            secrets[key] = ""
    secrets.clear()


def _exec_command(command: list[str]) -> None:
    cmd = command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise SystemExit("FAIL: no command provided")
    _scrub_infisical_env()
    os.execvp(cmd[0], cmd)


def cmd_fetch(args: argparse.Namespace) -> None:
    if _paths_cover(args.paths):
        _exec_command(args.command)

    bootstrap = load_bootstrap(args.bootstrap_path)
    env_vars = fetch_secrets(args.paths, bootstrap=bootstrap)

    for key, val in env_vars.items():
        os.environ[key] = val

    os.environ["HOMELAB_INFISICAL_PATHS"] = args.paths
    os.environ["HOMELAB_INFISICAL_LOADED"] = "1"

    _scrub_infisical_env()
    _exec_command(args.command)


def cmd_require(args: argparse.Namespace) -> None:
    keys = tuple(split_csv(args.keys))
    missing: list[str] = []
    for k in keys:
        resolvable, _ = secret_resolvable(k)
        if not resolvable:
            missing.append(k)

    if not missing:
        return

    if _paths_cover(args.paths):
        raise SystemExit(f"FAIL: missing required secret(s): {', '.join(missing)}")

    if not args.reexec:
        raise SystemExit(f"FAIL: missing required secret(s): {', '.join(missing)}")

    bootstrap = load_bootstrap(args.bootstrap_path)
    union = _paths_union(os.environ.get("HOMELAB_INFISICAL_PATHS", ""), args.paths)
    env_vars = fetch_secrets(union, bootstrap=bootstrap, required_keys=keys)

    for key, val in env_vars.items():
        os.environ[key] = val

    os.environ["HOMELAB_INFISICAL_PATHS"] = union
    os.environ["HOMELAB_INFISICAL_LOADED"] = "1"

    _scrub_infisical_env()
    os.execvp(args.reexec[0], args.reexec)


def cmd_run(args: argparse.Namespace) -> None:
    keys: tuple[str, ...] = ()
    if args.keys:
        keys = tuple(split_csv(args.keys))
        missing: list[str] = []
        for k in keys:
            resolvable, _ = secret_resolvable(k)
            if not resolvable:
                missing.append(k)
        if not missing:
            _exec_command(args.command)
        if _paths_cover(args.paths):
            raise SystemExit(f"FAIL: missing required secret(s): {', '.join(missing)}")
    elif _paths_cover(args.paths):
        _exec_command(args.command)

    bootstrap = load_bootstrap(args.bootstrap_path)
    env_vars = fetch_secrets(args.paths, bootstrap=bootstrap, required_keys=keys)

    for key, val in env_vars.items():
        os.environ[key] = val

    os.environ["HOMELAB_INFISICAL_PATHS"] = args.paths
    os.environ["HOMELAB_INFISICAL_LOADED"] = "1"

    _scrub_infisical_env()
    _exec_command(args.command)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Infisical secret fetcher (stdlib REST client)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_fetch = subparsers.add_parser("fetch", help="Fetch secrets and exec command")
    p_fetch.add_argument("--paths", required=True, help="Comma-separated Infisical paths")
    p_fetch.add_argument("--bootstrap", help="Path to bootstrap env file")
    p_fetch.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute")
    p_fetch.set_defaults(func=cmd_fetch)

    p_require = subparsers.add_parser("require", help="Check keys and fetch if needed")
    p_require.add_argument("--paths", required=True, help="Comma-separated Infisical paths")
    p_require.add_argument("--keys", required=True, help="Comma-separated required key names")
    p_require.add_argument("--bootstrap", help="Path to bootstrap env file")
    p_require.add_argument("--reexec", nargs=argparse.REMAINDER, help="Command to re-exec with secrets")
    p_require.set_defaults(func=cmd_require)

    p_run = subparsers.add_parser("run", help="Fetch secrets and exec command (run-with-secrets replacement)")
    p_run.add_argument("--paths", required=True, help="Comma-separated Infisical paths")
    p_run.add_argument("--keys", help="Comma-separated keys to check before fetching")
    p_run.add_argument("--bootstrap", help="Path to bootstrap env file")
    p_run.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.bootstrap_path = getattr(args, "bootstrap", None)
    args.func(args)


if __name__ == "__main__":
    main()
