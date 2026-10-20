from __future__ import annotations

from pathlib import Path
import re


TEMPLATES = Path(__file__).parents[2] / "config/infisical/templates"
# The Infisical project's slug; "homelab" 404s, so no secret file synced while the templates used it.
SLUG = "homelab-r-tg-j"


def test_every_template_reads_its_own_key_from_the_project():
    templates = sorted(TEMPLATES.glob("*.tmpl"))
    assert templates
    for template in templates:
        text = template.read_text()
        assert re.findall(r'listSecretsByProjectSlug "([^"]+)"', text) == [SLUG], template.name
        assert re.findall(r'eq \.Key "([^"]+)"', text) == [template.stem], template.name
