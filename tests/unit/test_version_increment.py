import subprocess
import sys
import unittest
from pathlib import Path

from scripts.check_version_increment import parse_version


class VersionIncrementTest(unittest.TestCase):
    def test_parses_version_as_numeric_tuple(self):
        self.assertEqual(parse_version("1.10.2\n", ".version"), (1, 10, 2))

    def test_numeric_tuple_orders_semver_components(self):
        self.assertGreater(
            parse_version("1.10.0", "current"),
            parse_version("1.9.9", "base"),
        )
        self.assertGreater(
            parse_version("2.0.0", "current"),
            parse_version("1.99.99", "base"),
        )

    def test_rejects_non_release_version(self):
        with self.assertRaisesRegex(ValueError, "plain semver"):
            parse_version("1.2.0-rc1", ".version")

    def test_cli_accepts_an_explicit_older_base_version(self):
        root = Path(__file__).parents[2]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "check_version_increment.py"),
                "--base-version",
                "1.5.3",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1.5.3 -> 1.5.4", result.stdout)


if __name__ == "__main__":
    unittest.main()
