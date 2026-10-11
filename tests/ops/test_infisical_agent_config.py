#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import managed_secrets


CONFIG_PATH = ROOT / "config" / "infisical" / "agent-config.yaml.example"
TEMPLATE_DIR = ROOT / "config" / "infisical" / "templates"
WORKTREE_MARKER = "/.worktrees/"
FORBIDDEN_CONFIG_TEXT = (
    "INFISICAL_CLIENT_ID",
    "INFISICAL_CLIENT_SECRET",
    "INFISICAL_PROJECT_ID",
)
ALLOWED_LITERAL_VALUES = frozenset(
    {
        "homelab",
        "prod",
        "60s",
        ".Value",
        "listSecretsByProjectSlug",
        "command",
        "commands",
        "destination-path",
        "polling-interval",
        "source-path",
        "${HOMELAB_ROOT}/scripts/ops/infisical-agent-reload.py",
        "${HOMELAB_ROOT}/config/infisical/templates",
        "${HOME}/.config/homelab/secrets",
        "/arr",
        "/llm",
        "/media",
        "/network",
        "/qbt",
    }
)
SECRET_LITERAL_RE = re.compile(r"[A-Za-z0-9_./+=-]{12,}")


class InfisicalAgentConfigTests(unittest.TestCase):
    def _assert_no_secret_like_literals(self, text: str, *, label: str) -> None:
        allowed = set(ALLOWED_LITERAL_VALUES)
        allowed.update(managed_secrets.managed_secret_keys())
        allowed.update(f"{key}.tmpl" for key in managed_secrets.managed_secret_keys())

        suspicious = []
        for candidate in SECRET_LITERAL_RE.findall(text):
            if candidate in allowed:
                continue
            upper_candidate = candidate.upper()
            if candidate.startswith("/") and candidate.count("/") >= 2:
                continue
            if "{{" in candidate or "}}" in candidate:
                continue
            if not any(marker in upper_candidate for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
                continue
            if upper_candidate in allowed:
                continue
            suspicious.append(candidate)

        self.assertEqual(
            suspicious,
            [],
            f"{label} contains secret-like literal values: {suspicious}",
        )

    def test_agent_templates_match_managed_secret_catalog(self) -> None:
        config_text = CONFIG_PATH.read_text(encoding="utf-8")
        config = yaml.safe_load(config_text)
        self.assertIsInstance(config, dict)
        self._assert_no_secret_like_literals(config_text, label=str(CONFIG_PATH))

        template_entries = config.get("templates")
        self.assertIsInstance(template_entries, list)

        entries_by_key = {}
        for entry in template_entries:
            self.assertIsInstance(entry, dict)
            destination = Path(entry["destination-path"])
            key = destination.name
            self.assertNotIn(key, entries_by_key, f"duplicate config destination for {key}")
            entries_by_key[key] = entry

        expected_pairs = managed_secrets.MANAGED_SECRET_KEY_PATH_PAIRS
        self.assertEqual(len(entries_by_key), len(expected_pairs))

        for path, key in expected_pairs:
            template_path = TEMPLATE_DIR / f"{key}.tmpl"
            self.assertTrue(template_path.is_file(), f"missing template for {key}")

            entry = entries_by_key.get(key)
            self.assertIsNotNone(entry, f"missing config entry for {key}")
            self.assertEqual(
                entry["source-path"],
                f"${{HOMELAB_ROOT}}/config/infisical/templates/{key}.tmpl",
            )
            self.assertEqual(
                entry["destination-path"],
                f"${{HOME}}/.config/homelab/secrets/{key}",
            )
            self.assertEqual(entry["polling-interval"], "60s")
            self.assertEqual(
                entry["commands"],
                [
                    {
                        "command": (
                            "${HOMELAB_ROOT}/scripts/ops/infisical-agent-reload.py "
                            f"--key {key}"
                        )
                    }
                ],
            )

            template_text = template_path.read_text(encoding="utf-8")
            self._assert_no_secret_like_literals(template_text, label=str(template_path))
            self.assertEqual(
                template_text,
                "\n".join(
                    (
                        f'{{{{- with listSecretsByProjectSlug "homelab" "prod" "{path}" -}}}}',
                        "{{- range . -}}",
                        f'{{{{- if eq .Key "{key}" -}}}}{{{{ .Value }}}}{{{{ end -}}}}',
                        "{{- end -}}",
                        "{{- end -}}",
                        "",
                    )
                ),
            )

        self.assertEqual(
            sorted(path.name for path in TEMPLATE_DIR.glob("*.tmpl")),
            sorted(f"{key}.tmpl" for _, key in expected_pairs),
        )
        self.assertNotIn(WORKTREE_MARKER, config_text)
        for template_path in TEMPLATE_DIR.glob("*.tmpl"):
            self.assertNotIn(WORKTREE_MARKER, template_path.read_text(encoding="utf-8"))
        for forbidden in FORBIDDEN_CONFIG_TEXT:
            self.assertNotIn(forbidden, config_text)


if __name__ == "__main__":
    unittest.main()
