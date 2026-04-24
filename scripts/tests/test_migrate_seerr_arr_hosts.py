"""Tests for scripts/migrate-seerr-arr-hosts.py (Readarr + clone settings path)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "migrate-seerr-arr-hosts.py"


def _write_minimal_config_xml(path: Path, url_base: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("Config")
    el = ET.SubElement(root, "UrlBase")
    el.text = url_base
    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def test_migrate_readarr_dry_run(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write_minimal_config_xml(data / "readarr" / "config.xml", "/readarr")
    _write_minimal_config_xml(data / "sonarr" / "config.xml", "/sonarr")
    _write_minimal_config_xml(data / "radarr" / "config.xml", "/radarr")
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "readarr": [
                    {
                        "name": "local",
                        "hostname": "127.0.0.1",
                        "baseUrl": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--settings",
            str(settings),
            "--data-dir",
            str(data),
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "readarr" in proc.stdout
    body = json.loads(settings.read_text(encoding="utf-8"))
    assert body["readarr"][0]["hostname"] == "127.0.0.1"


def test_migrate_readarr_apply(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write_minimal_config_xml(data / "readarr" / "config.xml", "/readarr")
    _write_minimal_config_xml(data / "sonarr" / "config.xml", "/sonarr")
    _write_minimal_config_xml(data / "radarr" / "config.xml", "/radarr")
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "readarr": [
                    {"name": "r", "hostname": "127.0.0.1", "baseUrl": "/old"},
                ]
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--settings",
            str(settings),
            "--data-dir",
            str(data),
            "--readarr-host",
            "readarr",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    body = json.loads(settings.read_text(encoding="utf-8"))
    assert body["readarr"][0]["hostname"] == "readarr"
    assert body["readarr"][0]["baseUrl"] == "/readarr"
