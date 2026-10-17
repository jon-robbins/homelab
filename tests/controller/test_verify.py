import json
from pathlib import Path

import pytest

from scripts.controller.admission import HostPolicy, load_host_policy, verify_record
from scripts.release.bundle import build, bundle_sha256


FIXTURES = Path(__file__).parent / "fixtures"


def policy(root, feed):
    return HostPolicy("test-site", "identity", "issuer", root, feed)


def setup_record(tmp_path):
    root = tmp_path / "root.json"
    root.write_text("{}")
    feed = tmp_path / "feed"
    record_dir = feed / "records" / "1"
    record_dir.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "bundle-source"
    (source / "files").mkdir(parents=True, exist_ok=True)
    for name in ("applications.json", "compose.yaml", "probe-targets.json"):
        (source / name).write_bytes(b"{}")
    bundle = build(source)
    record = json.loads((FIXTURES / "record.json").read_text())
    record["bundle_sha256"] = bundle_sha256(bundle)
    (record_dir / "record.json").write_text(json.dumps(record, separators=(",", ":")))
    (record_dir / "record.sigstore.json").write_bytes((FIXTURES / "record.sigstore.json").read_bytes())
    (record_dir / "bundle.tar").write_bytes(bundle)
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


def test_policy_rejects_duplicate_schema_key(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"schema":2,"schema":1,"site_id":"test-site",'
                    '"signer_identity":"identity","oidc_issuer":"issuer",'
                    '"trusted_root":"/root","feed_directory":"/feed"}')
    with pytest.raises(ValueError, match="invalid host policy") as error:
        load_host_policy(path)
    assert "duplicate object key" in str(error.value.__cause__)


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
        if identity != "identity" or issuer != "issuer" or record.read_bytes() != (record_dir / "record.json").read_bytes():
            raise ValueError("signature rejected")

    with pytest.raises((ValueError, OSError)):
        verify_record(record_dir, configured, verifier)
    expected = ["record.json", "bundle.tar"] if failure == "missing" else [name for name, _ in before]
    assert sorted(path.name for path in record_dir.iterdir()) == sorted(expected)


def test_verified_record_returns_original_bytes_and_digest(tmp_path):
    import hashlib

    record_dir, configured = setup_record(tmp_path)
    observed = []

    def verifier(record, bundle, **kwargs):
        observed.append((record.read_bytes(), kwargs))

    value, raw, signature, digest = verify_record(record_dir, configured, verifier)
    assert value["site_id"] == "test-site"
    assert raw == (record_dir / "record.json").read_bytes()
    assert signature == (FIXTURES / "record.sigstore.json").read_bytes()
    assert digest == hashlib.sha256(raw).hexdigest()
    assert observed[0][0] == raw


def test_file_replacement_during_verification_uses_snapshot(tmp_path):
    import hashlib

    record_dir, configured = setup_record(tmp_path)
    original_record = (record_dir / "record.json").read_bytes()
    original_signature = (record_dir / "record.sigstore.json").read_bytes()
    snapshot_paths = []

    def verifier(record, bundle, **kwargs):
        snapshot_paths.extend((record, bundle))
        replacement_record = record_dir / "replacement.json"
        replacement_bundle = record_dir / "replacement.sigstore.json"
        replacement_record.write_bytes(b"invalid replacement record")
        replacement_bundle.write_bytes(b"invalid replacement bundle")
        replacement_record.replace(record_dir / "record.json")
        replacement_bundle.replace(record_dir / "record.sigstore.json")
        assert record.read_bytes() == original_record
        assert bundle.read_bytes() == original_signature

    value, raw, signature, digest = verify_record(record_dir, configured, verifier)
    assert value["site_id"] == "test-site"
    assert (raw, signature, digest) == (original_record, original_signature,
                                        hashlib.sha256(original_record).hexdigest())
    assert not any(path.exists() for path in snapshot_paths)


def test_site_mismatch_is_rejected_after_verification(tmp_path):
    record_dir, configured = setup_record(tmp_path)
    calls = []
    with pytest.raises(ValueError, match="site_id"):
        verify_record(record_dir, HostPolicy("other", configured.signer_identity,
                      configured.oidc_issuer, configured.trusted_root, configured.feed_directory),
                      lambda *args, **kwargs: calls.append(True))
    assert calls == [True]


def test_bundle_is_required_and_matches_record_digest(tmp_path):
    record_dir, configured = setup_record(tmp_path)
    (record_dir / "bundle.tar").unlink()
    with pytest.raises(OSError):
        verify_record(record_dir, configured, lambda *args, **kwargs: None)

    record_dir, configured = setup_record(tmp_path)
    (record_dir / "bundle.tar").write_bytes(b"not a v2 bundle")
    with pytest.raises(ValueError, match="tar archive"):
        verify_record(record_dir, configured, lambda *args, **kwargs: None)

    record_dir, configured = setup_record(tmp_path)
    record = json.loads((record_dir / "record.json").read_text())
    record["bundle_sha256"] = "sha256:" + "0" * 64
    (record_dir / "record.json").write_text(json.dumps(record, separators=(",", ":")))
    with pytest.raises(ValueError, match="bundle_sha256"):
        verify_record(record_dir, configured, lambda *args, **kwargs: None)
