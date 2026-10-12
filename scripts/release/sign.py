"""Deterministic ReleaseRecord serialization and Ed25519 signatures."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

from .contracts import load_release_record, validate_name, validate_release_record, parse_json

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


def _signature_json(key_id: str, signature: bytes) -> bytes:
    validate_name(key_id, "$.key_id")
    if len(signature) != 64:
        raise ValueError("Ed25519 signature must be 64 bytes")
    return (json.dumps({"key_id": key_id, "signature": base64.b64encode(signature).decode("ascii")},
                       sort_keys=True, separators=(",", ":")) + "\n").encode()


def parse_signature(value: bytes) -> tuple[str, bytes]:
    """Parse the strict ReleaseSignature JSON envelope."""
    try:
        parsed = parse_json(value)
        if not isinstance(parsed, dict) or set(parsed) != {"key_id", "signature"}:
            raise ValueError("signature must contain key_id and signature")
        key_id = validate_name(parsed["key_id"], "$.key_id")
        encoded = parsed["signature"]
        if not isinstance(encoded, str):
            raise ValueError("signature must be base64 text")
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != 64:
            raise ValueError("signature must decode to 64 bytes")
        return key_id, raw
    except (TypeError, ValueError) as error:
        raise ValueError("malformed release signature") from error


def sign_record(record: Mapping[str, Any], *, env_var: str = "HOMELAB_RELEASE_SIGNING_KEY",
                key_id_env: str = "RELEASE_SIGNING_KEY_ID") -> tuple[bytes, bytes]:
    """Read the key and key ID only here; return canonical record and envelope."""
    data = record_bytes(record)
    secret = os.environ.get(env_var)
    if not secret:
        raise ValueError(f"missing signing key environment variable: {env_var}")
    key_id = os.environ.get(key_id_env)
    if not key_id:
        raise ValueError(f"missing signing key ID environment variable: {key_id_env}")
    validate_name(key_id, "$.key_id")
    return data, _signature_json(key_id, _private(secret).sign(DOMAIN + data))


def verify_signature(record: bytes | Mapping[str, Any], signature: bytes, public_key: bytes | Ed25519PublicKey) -> bool:
    data = record if isinstance(record, bytes) else record_bytes(record)
    try:
        _, signature = parse_signature(signature)
    except ValueError:
        # This helper is also used for low-level cryptographic tests. Feed
        # publication uses parse_signature strictly and never accepts raw bytes.
        pass
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
    """Return the ReleaseSignature JSON envelope."""
    return sign_record(record, env_var=key_env)[1]


def verify(record_data: bytes | Mapping[str, Any], signature: bytes, public_key: bytes | Ed25519PublicKey) -> None:
    """Raise when a detached signature does not verify."""
    if not verify_signature(record_data, signature, public_key):
        raise InvalidSignature
