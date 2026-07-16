import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import joplin_md_sync

REPO = Path(__file__).resolve().parents[2]


class VersionSourceTest(unittest.TestCase):
    def test_version_file_is_single_source(self):
        version_file = (REPO / ".version").read_text(encoding="utf-8").strip()
        self.assertRegex(version_file, r"^\d+\.\d+\.\d+$")
        self.assertEqual(joplin_md_sync.__version__, version_file)

    def test_manifest_matches_version_file(self):
        import json

        manifest = json.loads((REPO / "agent-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], joplin_md_sync.__version__)

    def test_pyproject_reads_version_dynamically(self):
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertIn('{ file = ".version" }', pyproject)
        # No hardcoded literal version anywhere in project metadata.
        self.assertNotRegex(pyproject, re.compile(r'^version = "', re.M))


if __name__ == "__main__":
    unittest.main()
