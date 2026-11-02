"""The Traefik edge module renders through the real renderer and policy, and the platform config keeps its invariants.

A site selects `traefik` instead of `caddy`; nothing else about the edge tier changes (0020).
"""

from pathlib import Path

import yaml

from scripts.release.modules import read_env
from scripts.release.render import render

ROOT = Path(__file__).parents[2]
SITE = Path(__file__).parent / "site_traefik"
ENV = read_env(SITE / "site.env")
CONFIG = ROOT / "config/traefik"


def _traefik():
    return render(ROOT, SITE, {}).edge["services"]["traefik"]


def test_site_selecting_traefik_renders_the_edge_tier_without_caddy():
    assert set(render(ROOT, SITE, {}).edge["services"]) == {"traefik", "cloudflared"}


def test_traefik_runs_unprivileged_and_publishes_only_the_tailnet_entry_point():
    service = _traefik()
    assert service["user"] == "1000:1000"
    assert service["cap_drop"] == ["ALL"] and "cap_add" not in service
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert [(p["host_ip"], p["published"], p["target"]) for p in service["ports"]] == [(ENV["TAILSCALE_IP"], "443", 4443)]
    assert not any("docker.sock" in str(v) for v in service["volumes"])


def test_cloudflare_token_reaches_traefik_only_as_a_file_path():
    service = _traefik()
    assert service["environment"]["CF_DNS_API_TOKEN_FILE"] == "/run/secrets/CLOUDFLARE_TOKEN"
    assert not any("TOKEN" in key and not key.endswith("_FILE") for key in service["environment"])
    assert [secret["source"] for secret in service["secrets"]] == ["CLOUDFLARE_TOKEN"]


def test_traefik_gets_platform_static_and_health_config_and_the_sites_routes():
    result = render(ROOT, SITE, {}).edge
    contents = {c["target"]: result["configs"][c["source"]]["content"] for c in _traefik()["configs"]}
    assert set(contents) == {
        "/etc/traefik/traefik.yml",
        "/etc/traefik/dynamic/health.yml",
        "/etc/traefik/dynamic/routes.yml",
    }
    for target, source in {
        "/etc/traefik/dynamic/routes.yml": SITE / "edge/traefik.yml",
        "/etc/traefik/dynamic/health.yml": CONFIG / "health.yml",
        "/etc/traefik/traefik.yml": CONFIG / "traefik.yml",
    }.items():
        assert contents[target] == source.read_text().replace("$", "$$"), target


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


def test_example_routes_are_consistent_and_only_public_routes_join_the_tunnel():
    http = yaml.safe_load((CONFIG / "routes.example.yml").read_text().replace("{{", "").replace("}}", ""))["http"]
    for name, router in http["routers"].items():
        assert router["service"] in http["services"] or router["service"].endswith("@internal"), name
        assert set(router.get("middlewares", [])) <= set(http["middlewares"]), name
    assert [n for n, r in http["routers"].items() if "tunnel" in r.get("entryPoints", [])] == ["requests"]
