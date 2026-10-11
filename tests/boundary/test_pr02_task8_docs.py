from pathlib import Path
import re
import unittest

from tests.private_markers import (
    assert_no_generic_private_markers,
    assert_no_private_markers,
)

ROOT = Path(__file__).resolve().parents[2]
DOCS = (
    ROOT / "docs/architecture/overview.md",
    ROOT / "docs/architecture/threat-model.md",
    ROOT / "docs/getting-started.md",
    ROOT / "README.md",
)


class PublicDocsTests(unittest.TestCase):
    def test_required_docs_exist_and_links_resolve(self):
        for path in DOCS:
            self.assertTrue(path.is_file(), path)
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)#]+)(?:#[^)]+)?\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                self.assertTrue((path.parent / target).resolve().exists(), f"{path}: {target}")

    def test_public_docs_use_fictional_values_and_describe_two_services(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
        assert_no_private_markers(self, text, "public docs", (2, 13, 14, 15, 16))
        self.assertIn("example.homelab.local", text)
        self.assertIn("hello", text)
        self.assertIn("whoami", text)
        self.assertIn("demo_api_token", text)
        self.assertIn("/run/" + "se" + "crets/", text)
        self.assertIn("synthetic-demo-token-only", text)

    def test_public_docs_have_no_generic_private_markers(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
        assert_no_generic_private_markers(self, text, "public docs")


if __name__ == "__main__":
    unittest.main()
