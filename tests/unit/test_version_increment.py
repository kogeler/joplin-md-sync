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

    def test_cli_accepts_an_explicit_older_base_version(self):
        root = Path(__file__).parents[2]
        current = (root / ".version").read_text(encoding="utf-8").strip()
        major, minor, patch = parse_version(current, ".version")
        if patch:
            base = f"{major}.{minor}.{patch - 1}"
        elif minor:
            base = f"{major}.{minor - 1}.999999"
        else:
            base = f"{major - 1}.999999.999999"
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "check_version_increment.py"),
                "--base-version",
                base,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{base} -> {current}", result.stdout)

    def test_cli_accepts_an_unchanged_published_version_only(self):
        root = Path(__file__).parents[2]
        current = (root / ".version").read_text(encoding="utf-8").strip()
        major, minor, patch = parse_version(current, ".version")

        def run(*arguments: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(root / "scripts" / "check_version_increment.py"), *arguments],
                check=False,
                capture_output=True,
                text=True,
            )

        unchanged = run("--published-version", current)
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertIn(f"{current} is already published; no increment required", unchanged.stdout)

        different = run("--published-version", f"{major}.{minor}.{patch + 1}")
        self.assertEqual(different.returncode, 2)
        self.assertIn("must equal the already published version", different.stderr)

        same_base = run("--base-version", current)
        self.assertEqual(same_base.returncode, 2)
        self.assertIn("must be incremented", same_base.stderr)


if __name__ == "__main__":
    unittest.main()
