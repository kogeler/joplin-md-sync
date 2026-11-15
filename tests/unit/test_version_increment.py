import subprocess
import sys
import unittest
from pathlib import Path

from scripts.check_version_increment import parse_version


class VersionIncrementTest(unittest.TestCase):
    def test_parses_version_as_numeric_tuple(self):
        parts = (1, 10, 2)
        text = ".".join(str(part) for part in parts)
        self.assertEqual(parse_version(f"{text}\n", ".version"), parts)

    def test_numeric_tuple_orders_semver_components(self):
        cases = (
            ((1, 10, 0), (1, 9, 9)),
            ((2, 0, 0), (1, 99, 99)),
        )
        for current, base in cases:
            current_text = ".".join(str(part) for part in current)
            base_text = ".".join(str(part) for part in base)
            self.assertGreater(
                parse_version(current_text, "current"),
                parse_version(base_text, "base"),
            )

    def test_rejects_non_release_version(self):
        prerelease = ".".join(str(part) for part in (1, 2, 0)) + "-rc1"
        with self.assertRaisesRegex(ValueError, "plain semver"):
            parse_version(prerelease, ".version")

    def test_cli_requires_an_increase_unless_the_unchanged_version_is_published(self):
        root = Path(__file__).parents[2]
        current = (root / ".version").read_text(encoding="utf-8").strip()
        major, minor, patch = parse_version(current, ".version")
        older = f"{major - 1}.999999.999999" if major else "0.0.0"
        newer = f"{major}.{minor}.{patch + 1}"
        cases = (
            (("--base-version", current, "--published"), 0, "already published"),
            (("--base-version", current, "--latest-release", older), 0, f"{older} -> {current}"),
            (("--base-version", older, "--published"), 0, f"{older} -> {current}"),
            (("--base-version", older, "--latest-release", current), 0, f"{older} -> {current}"),
            (("--base-version", newer, "--published"), 2, "must be incremented"),
            (("--base-version", current, "--latest-release", current), 2, "must be incremented"),
            (("--base-version", current), 2, "--published"),
        )
        for arguments, code, message in cases:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(root / "scripts" / "check_version_increment.py"),
                        *arguments,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(message, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
