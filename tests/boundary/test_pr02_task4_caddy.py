from pathlib import Path
import re
import unittest

from tests.private_markers import (
    assert_no_generic_private_markers,
    assert_no_private_markers,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "config/caddy/Caddyfile"
PUBLIC_GUIDE = ROOT / "docs/caddy-guide.md"
NETWORK = ROOT / "compose/network.yml"


class CaddyBoundaryTests(unittest.TestCase):
    def test_public_source_is_canonical_and_private_example_is_absent(self):
        self.assertTrue(PUBLIC.is_file())
        self.assertFalse((ROOT / "config/caddy/Caddyfile.example").exists())

    def test_public_file_has_synthetic_safe_contract(self):
        public = PUBLIC.read_text(encoding="utf-8")
        assert_no_private_markers(self, public, PUBLIC, range(2, 13))
        self.assertIn("{$BASE_DOMAIN:example.homelab.local}", public)
        self.assertIn('respond "Not Found" 404', public)
        self.assertNotRegex(public, r"remote_ip\s+(?!127\.0\.0\.1\b)\S+")

    def test_public_file_has_no_generic_private_markers(self):
        assert_no_generic_private_markers(self, PUBLIC.read_text(encoding="utf-8"), PUBLIC)

    def test_arr_routes_preserve_prefix_and_non_base_routes_strip(self):
        public = PUBLIC.read_text(encoding="utf-8")
        for service in ("sonarr", "radarr", "prowlarr", "bazarr"):
            self.assertRegex(public, rf"handle /{service}\*\s*\{{\s*reverse_proxy")
        for service in ("overseerr", "qbittorrent", "autobrr"):
            self.assertIn(f"handle_path /{service}*", public)

    def test_runtime_wiring_is_explicit(self):
        network = NETWORK.read_text(encoding="utf-8")
        self.assertIn("../config/caddy/Caddyfile:/etc/caddy/Caddyfile:ro", network)

    def test_public_guide_contains_no_private_ingress_markers(self):
        guide = PUBLIC_GUIDE.read_text(encoding="utf-8")
        assert_no_private_markers(self, guide, PUBLIC_GUIDE, range(2, 13))

    def test_public_guide_has_no_generic_private_markers(self):
        assert_no_generic_private_markers(
            self, PUBLIC_GUIDE.read_text(encoding="utf-8"), PUBLIC_GUIDE
        )


if __name__ == "__main__":
    unittest.main()
