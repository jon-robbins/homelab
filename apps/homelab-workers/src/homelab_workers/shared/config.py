from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
import yaml

DEFAULT_WORKERS_YAML_PATHS = [
    Path("/config/workers.yaml"),
    Path("config/workers.yaml"),
    Path("../config/workers.yaml"),
]


def load_worker_yaml_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load and parse workers.yaml configuration if present.

    Precedence order:
    1. Explicit config_path argument
    2. WORKERS_CONFIG_PATH environment variable
    3. Standard candidate paths (/config/workers.yaml, config/workers.yaml, ../config/workers.yaml)
    """
    if config_path:
        p = Path(config_path)
        if p.is_file():
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    env_path = os.environ.get("WORKERS_CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

    for candidate in DEFAULT_WORKERS_YAML_PATHS:
        if candidate.is_file():
            try:
                data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

    return {}


def get_config_val(
    env: Mapping[str, str] | None,
    yaml_dict: dict[str, Any] | None,
    env_key: str,
    yaml_key: str,
    default: Any,
) -> Any:
    if env and env_key in env:
        val = env.get(env_key)
        if val is not None and str(val).strip() != "":
            return val
    if yaml_dict and isinstance(yaml_dict, dict) and yaml_key in yaml_dict:
        val = yaml_dict.get(yaml_key)
        if val is not None:
            return val
    return default


def get_config_bool(
    env: Mapping[str, str] | None,
    yaml_dict: dict[str, Any] | None,
    env_key: str,
    yaml_key: str,
    default: bool,
) -> bool:
    val = get_config_val(env, yaml_dict, env_key, yaml_key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        lowered = val.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def get_config_int(
    env: Mapping[str, str] | None,
    yaml_dict: dict[str, Any] | None,
    env_key: str,
    yaml_key: str,
    default: int,
) -> int:
    val = get_config_val(env, yaml_dict, env_key, yaml_key, default)
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_config_float(
    env: Mapping[str, str] | None,
    yaml_dict: dict[str, Any] | None,
    env_key: str,
    yaml_key: str,
    default: float,
) -> float:
    val = get_config_val(env, yaml_dict, env_key, yaml_key, default)
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_config_str(
    env: Mapping[str, str] | None,
    yaml_dict: dict[str, Any] | None,
    env_key: str,
    yaml_key: str,
    default: str,
) -> str:
    val = get_config_val(env, yaml_dict, env_key, yaml_key, default)
    if val is None:
        return default
    return str(val)
