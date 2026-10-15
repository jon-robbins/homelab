"""Off-host checks for the public Compose boundary."""

from pathlib import Path
import unittest

import yaml

from tests.private_markers import (
    assert_no_generic_private_markers,
    assert_no_private_markers,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_NAMES = (
    "arr.yml",
    "download.yml",
    "media.yml",
    "autobrr.yml",
    "workers.yml",
    "llm.yml",
    "network.yml",
)
PUBLIC = tuple(ROOT / "compose" / name for name in PUBLIC_NAMES)
AGGREGATE = ROOT / "compose" / "base.yml"


class ComposeBoundaryTests(unittest.TestCase):
    def test_public_compose_has_no_site_or_private_tracker_values(self):
        for path in (*PUBLIC, AGGREGATE):
            assert_no_private_markers(
                self, path.read_text(encoding="utf-8"), path, range(12, 22)
            )

    def test_public_compose_has_no_generic_private_markers(self):
        for path in (*PUBLIC, AGGREGATE):
            assert_no_generic_private_markers(self, path.read_text(encoding="utf-8"), path)

    def test_public_aggregate_excludes_private_paths(self):
        aggregate = yaml.safe_load(AGGREGATE.read_text(encoding="utf-8"))
        included = "\n".join(str(item) for item in aggregate.get("include", []))
        self.assertNotIn("homelab-site", included)
        self.assertNotIn("tracker-integrations", included)

    def test_public_compose_uses_synthetic_defaults(self):
        env = (ROOT / ("." + "env.example")).read_text(encoding="utf-8")
        expected_env = (
            "BASE_DOMAIN=example.homelab.local",
            "TAILSCALE_IP=127.0.0.1",
            "SITE_HOSTNAME=homelab-example",
            "APP_DATA_DIR=./example-data",
            "HOMELAB_SECRET_DIR=./example-secrets",
            "MEDIA_HDD_PATH=./example-data/media-hdd",
            "MEDIA_NVME_PATH=./example-data/media-nvme",
            "PLEX_DATA_PATH=./example-data/plex",
        )
        for value in expected_env:
            with self.subTest(value=value):
                self.assertIn(value, env)

        compose_text = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC)
        self.assertRegex(compose_text, r"TAILSCALE_IP:-127\.0\.0\.1")
        self.assertRegex(compose_text, r"\./example-data/[A-Za-z0-9_./-]+")

    def test_internal_dashboard_image_pinned_immutably(self):
        document = yaml.safe_load((ROOT / "compose/llm.yml").read_text(encoding="utf-8"))
        image = document["services"]["internal-dashboard"]["image"]
        self.assertRegex(
            image,
            r"^nginxinc/nginx-unprivileged:alpine@sha256:[a-f0-9]{64}$",
        )

    def test_public_compose_yaml_validity(self):
        for path in (*PUBLIC, AGGREGATE):
            with self.subTest(path=path):
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertIsInstance(document, dict)

    def test_included_compose_files_define_each_top_level_resource_once(self):
        aggregate = yaml.safe_load(AGGREGATE.read_text(encoding="utf-8"))
        included_names = [Path(item["path"]).name for item in aggregate["include"]]
        locations = {key: {} for key in ("services", "networks", "volumes", "secrets", "configs")}
        documents = [(AGGREGATE.name, aggregate)]
        documents.extend(
            (filename, yaml.safe_load((ROOT / "compose" / filename).read_text(encoding="utf-8")))
            for filename in included_names
        )
        for filename, document in documents:
            for key, definitions in locations.items():
                for name in document.get(key) or {}:
                    definitions.setdefault(name, []).append(filename)

        duplicates = {
            key: {name: files for name, files in definitions.items() if len(files) > 1}
            for key, definitions in locations.items()
        }
        duplicates = {key: values for key, values in duplicates.items() if values}
        self.assertEqual(duplicates, {}, f"top-level resources defined in multiple included files: {duplicates}")

    def test_services_preserve_healthchecks_and_topology(self):
        required = {
            "arr.yml": ("sonarr", "radarr"),
            "download.yml": ("qbittorrent",),
            "media.yml": ("plex",),
            "autobrr.yml": ("autobrr",),
            "network.yml": ("caddy",),
        }
        for filename, services in required.items():
            document = yaml.safe_load((ROOT / "compose" / filename).read_text(encoding="utf-8"))
            for name in services:
                service = document["services"][name]
                with self.subTest(file=filename, service=name):
                    self.assertTrue(service.get("healthcheck"), "healthcheck was removed")
                    networks = service.get("networks") or ()
                    if isinstance(networks, dict):
                        networks = tuple(networks)
                    self.assertIn("homelab_net", networks)
                    self.assertTrue(service.get("logging"), "logging config was removed")


if __name__ == "__main__":
    unittest.main()
