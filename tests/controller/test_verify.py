import json
from pathlib import Path

import pytest

from scripts.controller.admission import HostPolicy, load_host_policy, verify_record


FIXTURES = Path(__file__).parent / "fixtures"


def policy(root, feed):
    return HostPolicy("test-site", "identity", "issuer", root, feed)


def setup_record(tmp_path):
    root = tmp_path / "root.json"
    root.write_text("{}")
    feed = tmp_path / "feed"
    record_dir = feed / "records" / "1"
    record_dir.mkdir(parents=True)
    (record_dir / "record.json").write_bytes((FIXTURES / "record.json").read_bytes())
    (record_dir / "record.sigstore.json").write_bytes((FIXTURES / "record.sigstore.json").read_bytes())
    return record_dir, policy(root, feed)


@pytest.mark.parametrize("change", [
    lambda p: {**p, "extra": True},
    lambda p: {key: value for key, value in p.items() if key != "site_id"},
    lambda p: {**p, "schema": 2},
])
def test_policy_is_closed_and_versioned(tmp_path, change):
    data = {"schema": 1, "site_id": "test-site", "signer_identity": "identity",
            "oidc_issuer": "issuer", "trusted_root": "/root", "feed_directory": "/feed"}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(change(data)))
    with pytest.raises(ValueError):
        load_host_policy(path)


@pytest.mark.parametrize("failure", ["identity", "issuer", "tampered", "missing"])
def test_signature_rejections_write_nothing(tmp_path, failure):
    record_dir, configured = setup_record(tmp_path)
    before = sorted((path.name, path.read_bytes()) for path in record_dir.iterdir())
    if failure == "tampered":
        (record_dir / "record.json").write_bytes(b"tampered")
    if failure == "missing":
        (record_dir / "record.sigstore.json").unlink()
    if failure == "identity":
        configured = HostPolicy(configured.site_id, "wrong-identity", configured.oidc_issuer,
                                configured.trusted_root, configured.feed_directory)
    if failure == "issuer":
        configured = HostPolicy(configured.site_id, configured.signer_identity, "wrong-issuer",
                                configured.trusted_root, configured.feed_directory)

    def verifier(record, bundle, *, identity, issuer, offline, trusted_root):
        assert offline and trusted_root == configured.trusted_root
        assert bundle.is_file()
        if identity != "identity" or issuer != "issuer" or record.read_bytes() != (FIXTURES / "record.json").read_bytes():
            raise ValueError("signature rejected")

    with pytest.raises((ValueError, OSError)):
        verify_record(record_dir, configured, verifier)
    expected = ["record.json"] if failure == "missing" else [name for name, _ in before]
    assert sorted(path.name for path in record_dir.iterdir()) == sorted(expected)


def test_verified_record_returns_original_bytes_and_digest(tmp_path):
    import hashlib

    record_dir, configured = setup_record(tmp_path)
    observed = []

    def verifier(record, bundle, **kwargs):
        observed.append((record.read_bytes(), kwargs))

    value, raw, signature, digest = verify_record(record_dir, configured, verifier)
    assert value["site_id"] == "test-site"
    assert raw == (FIXTURES / "record.json").read_bytes()
    assert signature == (FIXTURES / "record.sigstore.json").read_bytes()
    assert digest == hashlib.sha256(raw).hexdigest()
    assert observed[0][0] == raw


def test_site_mismatch_is_rejected_after_verification(tmp_path):
    record_dir, configured = setup_record(tmp_path)
    calls = []
    with pytest.raises(ValueError, match="site_id"):
        verify_record(record_dir, HostPolicy("other", configured.signer_identity,
                      configured.oidc_issuer, configured.trusted_root, configured.feed_directory),
                      lambda *args, **kwargs: calls.append(True))
    assert calls == [True]
