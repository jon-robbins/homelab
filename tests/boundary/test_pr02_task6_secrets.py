from pathlib import Path
import subprocess
import unittest

from tests.private_markers import require_private_markers


ROOT = Path(__file__).resolve().parents[2]


class SecretsBoundaryTests(unittest.TestCase):
    def test_required_runtime_artifacts_are_ignored(self):
        patterns = require_private_markers(self, range(22, 45))
        ignore = ROOT / ".gitignore"
        self.assertTrue(ignore.is_file())
        text = ignore.read_text(encoding="utf-8")
        for pattern in patterns:
            self.assertIn(pattern, text, f"{pattern} missing from {ignore}")

    def test_no_runtime_secret_artifacts_are_tracked(self):
        private_markers = require_private_markers(self, range(22, 45))
        exact_paths = tuple(
            marker
            for marker in private_markers
            if not marker.startswith(("*", ".")) and not marker.endswith("/")
        )
        suffixes = tuple(marker for marker in private_markers if marker.startswith("."))
        directories = tuple(marker for marker in private_markers if marker.endswith("/"))
        env_prefix = "." + "env"
        env_patterns = tuple(marker for marker in private_markers if marker.startswith(env_prefix))
        env_example = next(
            (marker[1:] for marker in private_markers if marker.startswith("!")), None
        )
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=ROOT, text=False
        ).decode().split("\x00")
        violations = [
            path
            for path in tracked
            if path
            and (
                path in exact_paths
                or path == next((marker for marker in env_patterns if marker == env_prefix), None)
                or (
                    any(path.startswith(marker[:-1]) for marker in env_patterns if marker.endswith(".*"))
                    and path != env_example
                )
                or path.startswith(directories)
                or path.endswith(suffixes)
            )
        ]
        self.assertEqual(violations, [])

if __name__ == "__main__":
    unittest.main()
