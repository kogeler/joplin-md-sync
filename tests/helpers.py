"""Shared test utilities: temp workspaces, CLI invocation, server fixtures."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from joplin_md_sync.cli import main as cli_main
from tests.fake_joplin_server import FakeJoplinServer

TOKEN = "test-token-0123456789abcdef"


class CliResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def json(self) -> dict[str, Any]:
        return json.loads(self.stdout)


def run_cli(*argv: str, env: dict[str, str] | None = None) -> CliResult:
    """Invoke the CLI in-process with captured stdio and a scoped environment."""
    stdout, stderr = io.StringIO(), io.StringIO()
    saved_env = dict(os.environ)
    os.environ.pop("JOPLIN_TOKEN", None)
    os.environ.pop("JOPLIN_BASE_URL", None)
    os.environ.pop("JOPLIN_PORT", None)
    if env:
        os.environ.update(env)
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli_main(list(argv))
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
    return CliResult(code, stdout.getvalue(), stderr.getvalue())


class WorkspaceTestCase(unittest.TestCase):
    """Base class: one fake Joplin server + one temp workspace per test."""

    seed_remote = True

    def setUp(self) -> None:
        self.server = FakeJoplinServer(token=TOKEN).start()
        self.addCleanup(self.server.stop)
        self._tmp = tempfile.TemporaryDirectory(prefix="jms-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "notes"
        self.env = {"JOPLIN_TOKEN": TOKEN, "JOPLIN_BASE_URL": self.server.base_url}
        if self.seed_remote:
            self.seed()

    def seed(self) -> None:
        """Default remote fixture; override per test class as needed."""
        store = self.server.store
        self.folder_work = store.add_folder("Work")
        self.folder_personal = store.add_folder("Personal")
        self.note_k8s = store.add_note("Kubernetes", "# Cluster\n\nline one\n", self.folder_work)
        self.note_plans = store.add_note("Plans", "some plans\n", self.folder_personal)
        tag = store.add_tag("homelab")
        store.tag_note(tag, self.note_k8s)

    # --- convenience -------------------------------------------------------

    def cli(self, *argv: str, expect: int | None = None) -> CliResult:
        result = run_cli(*argv, env=self.env)
        if expect is not None:
            self.assertEqual(
                result.exit_code, expect,
                f"argv={argv}\nstdout={result.stdout}\nstderr={result.stderr}",
            )
        return result

    def init_and_pull(self) -> None:
        self.cli("init", "--root", str(self.root), expect=0)
        self.cli("pull", "--root", str(self.root), "--json", expect=0)

    def read_workspace_file(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def find_note_file(self, fragment: str) -> Path:
        matches = [
            p for p in self.root.rglob("*.md")
            if fragment in p.name and ".joplin-sync" not in p.parts
        ]
        if len(matches) != 1:
            raise AssertionError(f"expected exactly one match for {fragment!r}, got {matches}")
        return matches[0]

    def tree_digest(self) -> dict[str, str]:
        """Hash of every file outside .joplin-sync — for no-mutation assertions."""
        import hashlib

        digest: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if ".joplin-sync" in path.parts or path.is_dir():
                continue
            digest[str(path.relative_to(self.root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        return digest
