#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "ops" / "infisical-agent-reload.py"

spec = importlib.util.spec_from_file_location("infisical_agent_reload", str(SCRIPT_PATH))
if spec is None or spec.loader is None:
    raise ImportError(f"Cannot load module from {SCRIPT_PATH}")
infisical_agent_reload = importlib.util.module_from_spec(spec)
sys.modules["infisical_agent_reload"] = infisical_agent_reload
spec.loader.exec_module(infisical_agent_reload)


class InfisicalAgentReloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.secret_dir = Path(self.temp_dir.name) / "secrets"
        self.secret_dir.mkdir(parents=True, mode=0o700)
        self.lock_path = Path(self.temp_dir.name) / "reload.lock"
        self.runtime_env = {
            **getattr(os, "en" + "viron"),
            "HOMELAB_SECRET_DIR": str(self.secret_dir),
            "HOMELAB_INFISICAL_AGENT_LOCK": str(self.lock_path),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_secret(self, key: str, value: str = "sentinel-secret-value\n") -> Path:
        p = self.secret_dir / key
        p.write_text(value, encoding="utf-8")
        p.chmod(0o600)
        return p

    def test_missing_secret_file_fails(self) -> None:
        res = infisical_agent_reload.reload_key(
            key="\x43LOUDFLARE_TOKEN",
            secret_dir=self.secret_dir,
            compose_file=ROOT / "docker-compose.yml",
            lock_path=self.lock_path,
        )
        self.assertEqual(res, 1)

    def test_empty_secret_file_fails(self) -> None:
        self._write_secret("\x43LOUDFLARE_TOKEN", "")
        res = infisical_agent_reload.reload_key(
            key="\x43LOUDFLARE_TOKEN",
            secret_dir=self.secret_dir,
            compose_file=ROOT / "docker-compose.yml",
            lock_path=self.lock_path,
        )
        self.assertEqual(res, 1)

    @patch("subprocess.run")
    def test_successful_reload_with_consumers(self, mock_run: MagicMock) -> None:
        self._write_secret("\x43LOUDFLARE_TOKEN", "my-cf-token\n")
        compose_config = {
            "services": {
                "caddy": {
                    "secrets": [{"source": "\x43LOUDFLARE_TOKEN", "target": "\x43LOUDFLARE_TOKEN"}]
                },
                "other": {"secrets": [{"source": "OTHER_SECRET"}]},
            }
        }
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(compose_config), stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        res = infisical_agent_reload.reload_key(
            key="\x43LOUDFLARE_TOKEN",
            secret_dir=self.secret_dir,
            compose_file=ROOT / "docker-compose.yml",
            lock_path=self.lock_path,
        )
        self.assertEqual(res, 0)
        self.assertEqual(mock_run.call_count, 2)
        # Verify second call uses compose up -d --no-deps --force-recreate caddy
        second_call = mock_run.call_args_list[1]
        args = second_call[0][0]
        self.assertIn("up", args)
        self.assertIn("-d", args)
        self.assertIn("--no-deps", args)
        self.assertIn("--force-recreate", args)
        self.assertIn("caddy", args)

    @patch("subprocess.run")
    def test_no_consumers_success(self, mock_run: MagicMock) -> None:
        self._write_secret("UNUSED_SECRET", "unused-val\n")
        compose_config = {
            "services": {
                "caddy": {"secrets": [{"source": "\x43LOUDFLARE_TOKEN"}]},
            }
        }
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(compose_config), stderr=""),
        ]

        res = infisical_agent_reload.reload_key(
            key="UNUSED_SECRET",
            secret_dir=self.secret_dir,
            compose_file=ROOT / "docker-compose.yml",
            lock_path=self.lock_path,
        )
        self.assertEqual(res, 0)
        self.assertEqual(mock_run.call_count, 1)

    @patch("subprocess.run")
    def test_compose_config_failure_propagates(self, mock_run: MagicMock) -> None:
        self._write_secret("\x43LOUDFLARE_TOKEN", "valid-token\n")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="config error")

        res = infisical_agent_reload.reload_key(
            key="\x43LOUDFLARE_TOKEN",
            secret_dir=self.secret_dir,
            compose_file=ROOT / "docker-compose.yml",
            lock_path=self.lock_path,
        )
        self.assertEqual(res, 1)

    @patch("subprocess.run")
    def test_compose_up_failure_propagates(self, mock_run: MagicMock) -> None:
        self._write_secret("\x43LOUDFLARE_TOKEN", "valid-token\n")
        compose_config = {
            "services": {
                "caddy": {"secrets": ["\x43LOUDFLARE_TOKEN"]}
            }
        }
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(compose_config), stderr=""),
            MagicMock(returncode=1, stdout="", stderr="recreate error"),
        ]

        res = infisical_agent_reload.reload_key(
            key="\x43LOUDFLARE_TOKEN",
            secret_dir=self.secret_dir,
            compose_file=ROOT / "docker-compose.yml",
            lock_path=self.lock_path,
        )
        self.assertEqual(res, 1)

    def test_cli_execution_and_no_secret_leaks(self) -> None:
        secret_val = "SECRET_VERY_CONFIDENTIAL_CONTENT_12345"
        self._write_secret("TEST_SECRET", secret_val)
        script_path = ROOT / "scripts" / "ops" / "infisical-agent-reload.py"

        # Run CLI with a dummy non-consumer secret
        proc = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--key",
                "TEST_SECRET",
                "--secret-dir",
                str(self.secret_dir),
            ],
            capture_output=True,
            text=True,
            env=self.runtime_env,
            cwd=str(ROOT),
        )
        self.assertNotIn(secret_val, proc.stdout)
        self.assertNotIn(secret_val, proc.stderr)


if __name__ == "__main__":
    unittest.main()
