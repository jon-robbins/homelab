"""Load and validate the site-owned release public-key map."""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .contracts import validate_name


def load_trusted_keys(path: Path) -> dict[str, bytes]:
    """Return the site trust map as PEM bytes, rejecting malformed input."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid release trust map: {path}") from error
    if not isinstance(value, dict) or value.get("schema") != 1 or set(value) != {"schema", "keys"}:
        raise ValueError("release trust map must have schema 1 and keys")
    keys = value["keys"]
    if not isinstance(keys, dict) or not keys:
        raise ValueError("release trust map keys must be a non-empty object")
    result: dict[str, bytes] = {}
    for key_id, pem in keys.items():
        if not isinstance(key_id, str):
            raise ValueError("release trust map key IDs must be strings")
        validate_name(key_id, "$.keys key ID")
        if not isinstance(pem, str) or not pem.strip():
            raise ValueError(f"release trust map key is invalid: {key_id}")
        raw = pem.encode("utf-8")
        try:
            public = serialization.load_pem_public_key(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"release trust map key is invalid: {key_id}") from error
        if not isinstance(public, Ed25519PublicKey):
            raise ValueError(f"release trust map key is not Ed25519: {key_id}")
        result[key_id] = raw
    return result
