"""The pre-merge comparison: a change that takes away a GPU, a port or a service must be an error, not a diff line."""

from scripts.release.compare import compare, losses

PLEX = {
    "image": "plex@sha256:" + "a" * 64,
    "ports": [{"host_ip": "192.0.2.10", "published": "32400", "target": 32400, "protocol": "tcp"}],
    "gpus": [{"count": -1, "capabilities": ["gpu"]}],
    "volumes": [{"type": "bind", "source": "/media", "target": "/data", "read_only": True}],
    "environment": {"TZ": "UTC"},
    "labels": {"homelab.config-digest": "one"},
}


def _lost(old, new):
    return losses(compare(old, new))


def test_identical_renders_differ_in_nothing():
    assert compare({"plex": PLEX}, {"plex": dict(PLEX)}) == {}


def test_losing_the_gpu_is_an_error():
    without_gpu = {key: value for key, value in PLEX.items() if key != "gpus"}
    assert _lost({"plex": PLEX}, {"plex": without_gpu}) == [
        'plex: lost gpus {"capabilities": ["gpu"], "count": -1}'
    ]


def test_losing_a_published_port_is_an_error():
    assert [line for line in _lost({"plex": PLEX}, {"plex": {**PLEX, "ports": []}})] == [
        'plex: lost ports {"host_ip": "192.0.2.10", "protocol": "tcp", "published": "32400", "target": 32400}'
    ]


def test_moving_a_port_to_another_address_is_a_loss_of_the_old_one():
    moved = [{**PLEX["ports"][0], "host_ip": "198.51.100.7"}]
    assert len(_lost({"plex": PLEX}, {"plex": {**PLEX, "ports": moved}})) == 1


def test_omitted_address_to_ipv4_wildcard_is_reported_as_a_listener_change():
    bare_port = {key: value for key, value in PLEX["ports"][0].items() if key != "host_ip"}
    wildcard_port = {**PLEX["ports"][0], "host_ip": "0.0.0.0"}
    report = compare({"plex": {**PLEX, "ports": [bare_port]}},
                     {"plex": {**PLEX, "ports": [wildcard_port]}})
    assert report["plex"]["removed"] == [("ports", '{"protocol": "tcp", "published": "32400", "target": 32400}')]
    assert report["plex"]["added"] == [("ports", '{"host_ip": "0.0.0.0", "protocol": "tcp", "published": "32400", "target": 32400}')]
    assert _lost({"plex": {**PLEX, "ports": [bare_port]}},
                 {"plex": {**PLEX, "ports": [wildcard_port]}})


def test_scoped_ipv6_and_unscoped_ipv6_addresses_are_distinct_listeners():
    scoped = {**PLEX["ports"][0], "host_ip": "fe80::1%en0"}
    plain = {**PLEX["ports"][0], "host_ip": "fe80::1"}
    assert _lost({"plex": {**PLEX, "ports": [scoped]}},
                 {"plex": {**PLEX, "ports": [plain]}})


def test_port_order_does_not_matter():
    second = {"host_ip": "127.0.0.1", "published": "9443", "target": 5443, "protocol": "tcp"}
    assert compare({"traefik": {"ports": [PLEX["ports"][0], second]}}, {"traefik": {"ports": [second, PLEX["ports"][0]]}}) == {}


def test_a_vanished_service_is_an_error():
    assert _lost({"plex": PLEX}, {}) == ["plex: service is gone"]


def test_added_mounts_and_changed_digests_are_reported_but_not_errors():
    new = {**PLEX, "volumes": PLEX["volumes"] + [{"type": "bind", "source": "/empty", "target": "/data/tv"}],
           "labels": {"homelab.config-digest": "two"}}
    report = compare({"plex": PLEX}, {"plex": new})
    assert losses(report) == []
    assert ("labels.homelab.config-digest", '"two"') in map(tuple, report["plex"]["added"])
    assert ("labels.homelab.config-digest", '"one"') in map(tuple, report["plex"]["removed"])


def test_a_new_service_is_reported_but_not_an_error():
    assert _lost({}, {"plex": PLEX}) == []


def test_losing_a_legacy_gpu_reservation_is_an_error():
    old = {"plex": {"deploy": {"resources": {"reservations": {
        "devices": [{"driver": "nvidia", "count": -1, "capabilities": ["gpu"]}]
    }}}}}
    assert _lost(old, {"plex": {}})


def test_losing_a_gpu_runtime_is_an_error():
    assert _lost({"plex": {"runtime": "nvidia"}}, {"plex": {}}) == [
        'plex: lost runtime "nvidia"'
    ]


def test_cli_reports_lost_service_and_render_failure(monkeypatch, capsys):
    from scripts.release import compare as module
    from scripts.release.contracts import ValidationError

    def services(platform, site, plugins):
        return {"plex": PLEX} if str(site) == "old" else {}

    monkeypatch.setattr(module, "_services", services)
    assert module.main(["platform", "old", "platform", "new"]) == 1
    assert "service is gone" in capsys.readouterr().err

    def broken(*args):
        raise ValidationError("site.env", "missing required key")

    monkeypatch.setattr(module, "_services", broken)
    assert module.main(["platform", "old", "platform", "new"]) == 2
    assert "render failed" in capsys.readouterr().err


def test_each_platform_uses_its_own_renderer(tmp_path):
    from scripts.release.compare import _services

    platform = tmp_path / "platform"
    package = platform / "scripts" / "release"
    package.mkdir(parents=True)
    (platform / "scripts/__init__.py").touch()
    (package / "__init__.py").touch()
    (package / "render.py").write_text(
        'import json\nprint(json.dumps({"app": {"services": {"platform_renderer": {}}}, "edge": {}}))\n'
    )
    assert _services(platform, tmp_path / "site", {}) == {"platform_renderer": {}}
