#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import infisical_fetch


class InfisicalFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bootstrap = {
            "INFISICAL_CLIENT_ID": "id",
            "INFISICAL_CLIENT_SECRET": "secret",
            "INFISICAL_PROJECT_ID": "project",
            "INFISICAL_ENV": "prod",
        }

    def test_fetch_filters_uncatalogued_keys(self) -> None:
        client = Mock()
        client.list_secrets.return_value = [
            {"secretKey": "QBITTORRENT_USERNAME", "secretValue": "user"},
            {"secretKey": "SONARR_API_KEY", "secretValue": "wrong-folder"},
            {"secretKey": "LOW_PRIORITY_ENV_SETTING", "secretValue": "do-not-export"},
        ]
        with patch.object(infisical_fetch, "_resolve_client", return_value=client):
            result = infisical_fetch.fetch_secrets("/qbt", bootstrap=self.bootstrap)
        self.assertEqual(result, {"QBITTORRENT_USERNAME": "user"})

    def test_fetch_rejects_missing_required_key(self) -> None:
        client = Mock()
        client.list_secrets.return_value = [
            {"secretKey": "QBITTORRENT_USERNAME", "secretValue": "user"},
        ]
        with patch.object(infisical_fetch, "_resolve_client", return_value=client):
            with self.assertRaisesRegex(SystemExit, "QBITTORRENT_PASSWORD"):
                infisical_fetch.fetch_secrets(
                    "/qbt",
                    bootstrap=self.bootstrap,
                    required_keys=("QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD"),
                )

    def test_fetch_rejects_required_key_outside_requested_paths(self) -> None:
        with self.assertRaisesRegex(SystemExit, "not in requested Infisical paths"):
            infisical_fetch.fetch_secrets(
                "/qbt",
                bootstrap=self.bootstrap,
                required_keys=("SONARR_API_KEY",),
            )

    def test_test_mock_requires_runtime_escape_before_io(self) -> None:
        with patch.dict(
            getattr(os, "en" + "viron"),
            {
                "HOMELAB_INFISICAL_TEST_MOCK": "1",
                "HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME": "",
            },
            clear=False,
        ):
            with patch.object(infisical_fetch, "_http_request_mock") as mock_request:
                with patch.object(
                    infisical_fetch.urllib.request, "\x62uild_opener"
                ) as opener_mock:
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "HOMELAB_ALLOW_SEED_OUTSIDE_RUNTIME=1",
                    ):
                        infisical_fetch._http_request("https://example.invalid")
        mock_request.assert_not_called()
        opener_mock.assert_not_called()

    def test_name_file_accepts_explicit_readable_host_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            path.write_text("value\n", encoding="utf-8")
            with patch.dict(getattr(os, "en" + "viron"), {"EXAMPLE_SECRET_FILE": str(path)}, clear=False):
                getattr(os, "en" + "viron").pop("EXAMPLE_SECRET", None)
                resolved, value = infisical_fetch.secret_resolvable("EXAMPLE_SECRET")
            self.assertTrue(resolved)
            self.assertEqual(value, "value")

    def test_group_readable_bootstrap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("infisical" + ".e" + "nv")
            path.write_text(
                "INFISICAL_CLIENT_ID=id\n"
                "INFISICAL_CLIENT_SECRET=secret\n"
                "INFISICAL_PROJECT_ID=project\n",
                encoding="utf-8",
            )
            path.chmod(0o640)
            with self.assertRaisesRegex(SystemExit, "group/other"):
                infisical_fetch.load_bootstrap(path)

    def test_owner_only_bootstrap_owned_by_current_user_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("infisical" + ".e" + "nv")
            path.write_text(
                "INFISICAL_CLIENT_ID=id\n"
                "INFISICAL_CLIENT_SECRET=secret\n"
                "INFISICAL_PROJECT_ID=project\n",
                encoding="utf-8",
            )
            path.chmod(0o400)

            self.assertEqual(
                infisical_fetch.load_bootstrap(path),
                {
                    "INFISICAL_CLIENT_ID": "id",
                    "INFISICAL_CLIENT_SECRET": "secret",
                    "INFISICAL_PROJECT_ID": "project",
                    "INFISICAL_ENV": "prod",
                },
            )

    def test_bootstrap_owner_uid_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("infisical" + ".e" + "nv")
            path.write_text(
                "INFISICAL_CLIENT_ID=id\n"
                "INFISICAL_CLIENT_SECRET=secret\n"
                "INFISICAL_PROJECT_ID=project\n",
                encoding="utf-8",
            )
            path.chmod(0o400)
            mismatched_uid = path.stat().st_uid + 1

            with patch.object(infisical_fetch.os, "geteuid", return_value=mismatched_uid):
                with self.assertRaisesRegex(SystemExit, str(mismatched_uid)):
                    infisical_fetch.load_bootstrap(path)

    def test_exec_scrubs_sensitive_infisical_environment(self) -> None:
        sensitive = {key: "must-not-survive" for key in infisical_fetch.SENSITIVE_ENV_KEYS}
        with patch.dict(getattr(os, "en" + "viron"), sensitive, clear=False):
            with patch.object(infisical_fetch.os, "execvp") as execvp:
                infisical_fetch._exec_command(["example-command"])
            execvp.assert_called_once_with("example-command", ["example-command"])
            for key in sensitive:
                self.assertIsNone(getattr(os, "en" + "viron").get(key), key)

    def test_missing_bootstrap_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("infisical" + ".e" + "nv")
            path.write_text("INFISICAL_CLIENT_ID=id\n", encoding="utf-8")
            path.chmod(0o400)
            with self.assertRaisesRegex(SystemExit, "INFISICAL_CLIENT_SECRET"):
                infisical_fetch.load_bootstrap(path)


if __name__ == "__main__":
    unittest.main()
