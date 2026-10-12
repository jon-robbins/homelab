"""Deterministic ReleaseRecord serialization and Ed25519 signatures."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

from .contracts import load_release_record, validate_release_record

DOMAIN = b"homelab-release-v1\n"


def record_bytes(record: Mapping[str, Any]) -> bytes:
    validate_release_record(dict(record))
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _private(value: str) -> Ed25519PrivateKey:
    raw = value.encode()
    if b"BEGIN" in raw:
        return serialization.load_pem_private_key(raw, password=None)
    try:
        decoded = bytes.fromhex(value)
    except ValueError:
        decoded = base64.b64decode(value, validate=True)
    if len(decoded) != 32:
        raise ValueError("signing key must be an Ed25519 private key")
    return Ed25519PrivateKey.from_private_bytes(decoded)


def sign_record(record: Mapping[str, Any], *, env_var: str = "HOMELAB_RELEASE_SIGNING_KEY") -> tuple[bytes, bytes]:
    """Read the key only here; return exact record bytes and detached signature."""
    data = record_bytes(record)
    secret = os.environ.get(env_var)
    if not secret:
        raise ValueError(f"missing signing key environment variable: {env_var}")
    return data, _private(secret).sign(DOMAIN + data)


def verify_signature(record: bytes | Mapping[str, Any], signature: bytes, public_key: bytes | Ed25519PublicKey) -> bool:
    data = record if isinstance(record, bytes) else record_bytes(record)
    if isinstance(public_key, Ed25519PublicKey):
        key = public_key
    else:
        try:
            key = serialization.load_pem_public_key(public_key)
        except (ValueError, TypeError):
            key = Ed25519PublicKey.from_public_bytes(public_key)
    try:
        key.verify(signature, DOMAIN + data)
    except InvalidSignature:
        return False
    return True
def sign(record: Mapping[str, Any], *, key_env: str = "HOMELAB_RELEASE_SIGNING_KEY") -> bytes:
    """Return only the detached signature (the historical feed API)."""
    return sign_record(record, env_var=key_env)[1]


def verify(record_data: bytes | Mapping[str, Any], signature: bytes, public_key: bytes | Ed25519PublicKey) -> None:
    """Raise when a detached signature does not verify."""
    if not verify_signature(record_data, signature, public_key):
        raise InvalidSignature
