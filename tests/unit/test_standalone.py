import unittest

from scripts.build_standalone import standalone_architecture, standalone_name
from scripts.verify_release import STANDALONE_NAMES, check_standalones


class StandaloneNameTest(unittest.TestCase):
    def test_supported_platform_names(self):
        cases = [
            ("linux", "x86_64", "joplin-md-sync-linux-amd64"),
            ("linux", "aarch64", "joplin-md-sync-linux-arm64"),
            ("win32", "AMD64", "joplin-md-sync-windows-amd64.exe"),
        ]
        for system, machine, expected in cases:
            with self.subTest(system=system, machine=machine):
                self.assertEqual(standalone_name(system, machine), expected)

    def test_rejects_windows_arm64(self):
        with self.assertRaisesRegex(ValueError, "unsupported standalone target"):
            standalone_name("win32", "ARM64")

    def test_rejects_unknown_architecture(self):
        with self.assertRaisesRegex(ValueError, "unsupported standalone architecture"):
            standalone_architecture("mips")


class StandaloneInventoryTest(unittest.TestCase):
    def test_requires_all_standalones_when_requested(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            standalones = [Path(tmp) / name for name in sorted(STANDALONE_NAMES)]
            for standalone in standalones:
                standalone.touch()

            check_standalones(standalones, require_all=True)
            with self.assertRaises(SystemExit):
                check_standalones(standalones[:-1], require_all=True)


if __name__ == "__main__":
    unittest.main()
