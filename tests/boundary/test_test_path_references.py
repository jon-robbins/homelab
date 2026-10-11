from pathlib import Path
import unittest

from tests.private_markers import assert_no_private_markers

ROOT = Path(__file__).resolve().parents[2]


class TestPathReferenceGuardTests(unittest.TestCase):
    def test_tests_do_not_reference_off_repo_paths(self):
        for path in (ROOT / "tests").rglob("test_*.py"):
            text = path.read_text(encoding="utf-8")
            assert_no_private_markers(
                self, text, path.relative_to(ROOT), range(45, 48)
            )


if __name__ == "__main__":
    unittest.main()
