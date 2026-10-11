#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import managed_secrets


EXPECTED_PAIRS = (
    ("/network", "\x43LOUDFLARE_TOKEN"),
    ("/media", "TMDB_API_KEY"),
    ("/arr", "SONARR_API_KEY"),
    ("/arr", "RADARR_API_KEY"),
    ("/arr", "PROWLARR_API_KEY"),
    ("/arr", "OVERSEERR_API_KEY"),
    ("/arr", "AUTOBRR_API_KEY"),
    ("/qbt", "QBITTORRENT_USERNAME"),
    ("/qbt", "QBITTORRENT_PASSWORD"),
    ("/llm", "GEMINI_API_KEY"),
    ("/llm", "MCP_API_TOKEN"),
    ("/llm", "MEDIA_AGENT_TOKEN"),
    ("/llm", "HOMELAB_AGENT_API_TOKEN"),
    ("/llm", "VLLM_API_KEY"),
    ("/media", "PLEX_TOKEN"),
)


class ManagedSecretsTests(unittest.TestCase):
    def test_exact_catalog(self) -> None:
        self.assertEqual(managed_secrets.MANAGED_SECRET_KEY_PATH_PAIRS, EXPECTED_PAIRS)

    def test_names_are_unique(self) -> None:
        names = managed_secrets.managed_secret_keys()
        self.assertEqual(len(names), len(set(names)))

    def test_keys_for_paths_preserve_catalog_order(self) -> None:
        self.assertEqual(
            managed_secrets.managed_secret_keys_for_paths(["/qbt", "/network"]),
            (
                "\x43LOUDFLARE_TOKEN",
                "QBITTORRENT_USERNAME",
                "QBITTORRENT_PASSWORD",
            ),
        )

    def test_unknown_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown managed secret path"):
            managed_secrets.managed_secret_keys_for_paths(["/unknown"])

    def test_bazarr_api_key_is_not_managed(self) -> None:
        self.assertNotIn("BAZARR_API_KEY", managed_secrets.managed_secret_keys())
        with self.assertRaises(KeyError):
            managed_secrets.managed_secret_path_for_key("BAZARR_API_KEY")

    def test_pairs_cli_matches_shell_contract(self) -> None:
        output = subprocess.check_output(
            [sys.executable, str(ROOT / "scripts" / "lib" / "managed_secrets.py"), "pairs"],
            text=True,
        )
        self.assertEqual(
            output.splitlines(),
            [f"{path}\t{name}" for path, name in EXPECTED_PAIRS],
        )


if __name__ == "__main__":
    unittest.main()
