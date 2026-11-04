"""Invariants of the Traefik platform config that no other test covers (0020).

Compose hardening and the module's health are covered by the policy tests and the real-image test in
`tests/release/test_render_docker.py`; inlining the config files by `tests/release/test_render.py`.
"""

from pathlib import Path, PurePosixPath

import yaml

from scripts.release.render import render

ROOT = Path(__file__).parents[2]
SITE = Path(__file__).parent / "site_traefik"
CONFIG = ROOT / "config/traefik"


def _traefik():
    return render(ROOT, SITE, {}).edge["services"]["traefik"]


def test_cloudflare_token_reaches_traefik_only_as_a_file_path():
    service = _traefik()
    assert service["environment"]["CF_DNS_API_TOKEN_FILE"] == "/run/secrets/CLOUDFLARE_TOKEN"
    assert not any("TOKEN" in key and not key.endswith("_FILE") for key in service["environment"])
    assert [secret["source"] for secret in service["secrets"]] == ["CLOUDFLARE_TOKEN"]


def test_every_entry_point_drops_client_forwarding_and_alias_headers():
    """A new entry point that forgets these would let a client forge the source address or an identity header."""
    static = yaml.safe_load((CONFIG / "traefik.yml").read_text())
    for name, entry in static["entryPoints"].items():
        assert entry["forwardedHeaders"]["trustedIPs"] == [], name
        assert entry["http"]["aliasHeadersStrategy"] == "delete", name
        assert not any(v for v in entry["http"]["encodedCharacters"].values()), name
    defaults = [name for name, entry in static["entryPoints"].items() if entry.get("asDefault")]
    assert defaults == ["tailnet"]


def test_static_config_has_no_discovery_or_admin_surface():
    static = yaml.safe_load((CONFIG / "traefik.yml").read_text())
    assert set(static["providers"]) == {"file"}
    assert not {"api", "experimental", "pilot"} & set(static)


def test_example_routes_are_consistent_and_every_router_strips_identity_headers_first():
    http = yaml.safe_load((CONFIG / "routes.example.yml").read_text().replace("{{", "").replace("}}", ""))["http"]
    for name, router in http["routers"].items():
        assert router["service"] in http["services"] or router["service"].endswith("@internal"), name
        assert set(router.get("middlewares", [])) <= set(http["middlewares"]), name
        first = http["middlewares"][router["middlewares"][0]]
        assert {"Remote-User", "X-Authentik-Username"} <= set(first["headers"]["customRequestHeaders"]), name
        assert all(v == "" for v in first["headers"]["customRequestHeaders"].values()), name


def _acme_resolvers() -> dict:
    return yaml.safe_load((CONFIG / "traefik.yml").read_text())["certificatesResolvers"]


def test_one_acme_resolver_issues_rsa_certificates_on_the_x1_chain():
    """Plex needs an RSA certificate on the X1 chain. A second resolver would not isolate certificates (0020 §2)."""
    resolvers = _acme_resolvers()
    assert list(resolvers) == ["cloudflare"]
    assert resolvers["cloudflare"]["acme"]["keyType"] == "RSA2048"
    assert resolvers["cloudflare"]["acme"]["preferredChain"] == "ISRG Root X1"


def test_acme_storage_is_on_a_bind_mount_so_certificates_survive_a_recreate():
    """A release recreates the container, so the resolver's storage file must live on a host bind mount (0020 §4)."""
    storage = PurePosixPath(_acme_resolvers()["cloudflare"]["acme"]["storage"])
    binds = {volume["target"] for volume in _traefik()["volumes"] if volume["type"] == "bind"}
    assert str(storage.parent) in binds
