from __future__ import annotations

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).parents[2]
TEMPLATES = ROOT / "config/infisical/templates"
STAMP = ROOT / "host/stamp-infisical-templates"
PLACEHOLDER = "@INFISICAL_PROJECT_SLUG@"


def test_every_template_is_a_slug_skeleton():
    templates = sorted(TEMPLATES.glob("*.tmpl"))
    assert templates
    for template in templates:
        text = template.read_text()
        assert re.findall(r'listSecretsByProjectSlug "([^"]+)"', text) == [PLACEHOLDER], template.name
        assert re.findall(r'eq \.Key "([^"]+)"', text) == [template.stem], template.name


def test_stamp_infisical_templates_writes_slug(tmp_path: Path):
    dest = tmp_path / "out"
    slug_file = tmp_path / "project-slug"
    slug_file.write_text("example-project-slug\n")
    result = subprocess.run(
        [
            str(STAMP),
            "--slug-file",
            str(slug_file),
            "--from",
            str(TEMPLATES),
            "--to",
            str(dest),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    stamped = sorted(dest.glob("*.tmpl"))
    assert stamped
    for template in stamped:
        text = template.read_text()
        assert PLACEHOLDER not in text
        assert re.findall(r'listSecretsByProjectSlug "([^"]+)"', text) == ["example-project-slug"]
