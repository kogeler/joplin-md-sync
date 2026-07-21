import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "examples" / "agent-notes-repository"
INSTALLER_PATH = TEMPLATE / "scripts" / "install-joplin-md-sync.py"


def load_installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_notes_installer", INSTALLER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load agent notes installer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_installer()


class AgentNotesTemplateTest(unittest.TestCase):
    def test_required_files_and_ignored_local_state(self):
        for relative in (
            ".gitignore",
            "AGENTS.md",
            "README.md",
            "RUNBOOK.md",
            "scripts/install-joplin-md-sync.py",
        ):
            self.assertTrue((TEMPLATE / relative).is_file(), relative)

        gitignore = (TEMPLATE / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".tools/", gitignore)
        self.assertIn(".secrets/", gitignore)
        self.assertIn("**/.joplin-sync/", gitignore)

    def test_user_docs_lead_with_joplin_api_and_token_location(self):
        readme = (TEMPLATE / "README.md").read_text(encoding="utf-8")
        api_heading = readme.index("## 1. Enable the Joplin API")
        repository_heading = readme.index("## 2. Create this repository")
        self.assertLess(api_heading, repository_heading)
        self.assertIn("Tools > Options > Web Clipper", readme)
        self.assertIn(".secrets/joplin-token", readme)
        self.assertIn("--token-file ./.secrets/joplin-token", readme)

    def test_agent_contract_contains_guarded_file_and_mcp_workflows(self):
        runbook = (TEMPLATE / "AGENTS.md").read_text(encoding="utf-8")
        for required in (
            "pull --root ./notes",
            "diff --root ./notes --three-way --unified",
            "push --root ./notes --dry-run",
            "Never pass `--propagate-deletes`",
            "http://127.0.0.1:8765/mcp",
            '"type": "streamable-http"',
        ):
            self.assertIn(required, runbook)


class AgentNotesInstallerTest(unittest.TestCase):
    def test_supported_asset_names(self):
        cases = (
            ("linux", "x86_64", "joplin-md-sync-linux-amd64"),
            ("linux", "aarch64", "joplin-md-sync-linux-arm64"),
            ("win32", "AMD64", "joplin-md-sync-windows-amd64.exe"),
        )
        for system, machine, expected in cases:
            with self.subTest(system=system, machine=machine):
                self.assertEqual(installer.standalone_asset(system, machine), expected)

        with self.assertRaisesRegex(installer.InstallError, "no standalone release"):
            installer.standalone_asset("win32", "arm64")

    def test_release_inventory_and_urls_are_verified(self):
        asset_name = "joplin-md-sync-linux-amd64"
        prefix = "https://github.com/kogeler/joplin-md-sync/releases/download/v2.3.4"
        payload = {
            "draft": False,
            "prerelease": False,
            "tag_name": "v2.3.4",
            "assets": [
                {"name": asset_name, "browser_download_url": f"{prefix}/{asset_name}"},
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": f"{prefix}/SHA256SUMS.txt",
                },
            ],
        }
        release = installer.parse_release(payload, asset_name)
        self.assertEqual(release.version, "2.3.4")

        payload["assets"][0]["browser_download_url"] = "https://example.invalid/binary"
        with self.assertRaisesRegex(installer.InstallError, "unexpected URL"):
            installer.parse_release(payload, asset_name)

    def test_checksum_parser_requires_one_exact_entry(self):
        digest = "a" * 64
        contents = f"{'b' * 64}  other\n{digest}  wanted\n".encode("ascii")
        self.assertEqual(installer.parse_checksum(contents, "wanted"), digest)
        with self.assertRaisesRegex(installer.InstallError, "exactly one"):
            installer.parse_checksum(contents, "missing")


if __name__ == "__main__":
    unittest.main()
