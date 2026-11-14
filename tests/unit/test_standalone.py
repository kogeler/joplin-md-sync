import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.build_standalone import (
    CANDIDATE_VERSION_ENV,
    REPO,
    standalone_architecture,
    standalone_name,
    version_resource,
)
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


class CandidateVersionTest(unittest.TestCase):
    def test_release_build_bundles_the_repository_version(self):
        self.assertEqual(version_resource({}), REPO / ".version")

    def test_candidate_stamps_a_prerelease_of_the_repository_version(self):
        release = (REPO / ".version").read_text(encoding="utf-8").strip()
        candidate = f"{release}-pr48.g7bd6520"
        with TemporaryDirectory() as tmp:
            stamped = version_resource({CANDIDATE_VERSION_ENV: candidate}, Path(tmp))
            self.assertEqual(stamped.name, ".version")
            self.assertEqual(stamped.read_text(encoding="utf-8"), candidate + "\n")

    def test_candidate_rejects_other_versions_and_identifiers(self):
        release = (REPO / ".version").read_text(encoding="utf-8").strip()
        for candidate in (
            "9.9.9-pr48.g7bd6520",
            f"{release}-pr0.g7bd6520",
            f"{release}-pr48.7bd6520",
            f"{release}-pr48.gXYZ1234",
            f"{release}",
        ):
            with self.subTest(candidate=candidate), TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, CANDIDATE_VERSION_ENV):
                    version_resource({CANDIDATE_VERSION_ENV: candidate}, Path(tmp))


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
