"""Real installer processes/TTY/files/downloads; only external host programs are peers."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import http.server
import json
import os
import pty
import runpy
import select
import shlex
import socket
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
INSTALLER = ROOT / "joplin_terminal_service/install_joplin_terminal.py"
SYNC_SECRET = "private-sync-fixture-do-not-echo"
OAUTH_SECRET = "private-github-fixture-do-not-echo"
REPLACEMENT_SECRET = "private-replacement-sync-fixture-do-not-echo"

HOST_PROGRAM = r"""
import json, os, shutil, sys
from pathlib import Path
root = Path(os.environ['WIZARD_FIXTURE_ROOT'])
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root/'calls.jsonl').open('a') as log:
    log.write(json.dumps([tool, args])+'\n')
if tool == 'node':
    print('v22.0.0')
elif tool == 'npm':
    if args == ['--version']:
        print('10.0.0')
    elif args[0] == 'view':
        print('3.6.2')
    else:
        prefix = Path(args[args.index('--prefix')+1])
        marker = prefix/'fixture-version'
        if args[0] == 'list':
            print(json.dumps({'dependencies': {'joplin': {'version': marker.read_text()}}} if marker.exists() else {}))
        elif args[0] == 'install':
            (prefix/'bin').mkdir(parents=True, exist_ok=True)
            shutil.copy2(root/'bin/joplin', prefix/'bin/joplin')
            marker.write_text(args[-1].split('@')[-1])
        else:
            raise SystemExit(95)
elif tool == 'loginctl':
    print('no')
elif tool == 'systemctl':
    if 'is-active' in args:
        print('inactive')
        raise SystemExit(3)
    elif 'show-environment' in args:
        print('PATH='+os.environ['PATH'])
elif tool == 'joplin':
    profile = Path(args[args.index('--profile')+1])
    args = args[args.index('--profile')+2:]
    if args[0] == 'help':
        print(args[1]+' <command>')
        raise SystemExit(0)
    profile.mkdir(parents=True, exist_ok=True)
    location = profile/'fixture-settings.json'
    settings = json.loads(location.read_text()) if location.exists() else {
        'sync.target': 0, 'locale': 'en_GB', 'api.token': 'j'*43,
    }
    if args == ['config', '--import']:
        settings.update(json.load(sys.stdin))
    elif args == ['config', '--export']:
        print(json.dumps(settings))
    elif args[0] == 'config':
        name = args[1]
        value = settings.get(name, 'null')
        if name.endswith('.password') and value:
            value = '********'
        print(name+' = '+str(value))
    elif args == ['sync']:
        print('Completed: fixture sync')
    elif args == ['e2ee', 'status']:
        print('Encryption is: Disabled')
    elif args == ['status']:
        print('Fixture status')
    else:
        raise SystemExit(96)
    location.write_text(json.dumps(settings))
else:
    raise SystemExit(97)
"""


class WizardProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="installer-wizard-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        binaries = self.root / "bin"
        binaries.mkdir()
        for name in ("node", "npm", "systemctl", "loginctl", "joplin"):
            path = binaries / name
            path.write_text(f"#!{sys.executable}\n" + HOST_PROGRAM)
            path.chmod(0o700)
        version = (REPO / ".version").read_text().strip()
        self.version = version
        self.corrupt_bundle = False
        self.bundle_requests = []
        fixture = self
        candidate = (
            f"#!{sys.executable}\nimport sys\n"
            f"sys.path.insert(0, {str(REPO / 'src')!r})\nsys.frozen=True\n"
            "from joplin_md_sync.cli import main\nraise SystemExit(main())\n"
        ).encode()
        self.candidate = candidate

        class ReleasePeer(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path.startswith("/bundle/"):
                    relative = self.path.removeprefix("/bundle/")
                    if relative.startswith("refs/"):
                        encoded_ref, _separator, relative = relative.removeprefix(
                            "refs/"
                        ).partition("/")
                        fixture.bundle_requests.append(
                            (urllib.parse.unquote(encoded_ref), relative)
                        )
                    data = (ROOT / "joplin_terminal_service" / relative).read_bytes()
                    if fixture.corrupt_bundle and self.path.endswith("/cli.py"):
                        data = b"#" + data[1:]
                elif self.path == "/latest":
                    data = json.dumps({"tag_name": "v" + version}).encode()
                elif self.path.endswith("/SHA256SUMS.txt"):
                    data = (
                        hashlib.sha256(candidate).hexdigest()
                        + "  joplin-md-sync-linux-amd64\n"
                        + hashlib.sha256(candidate).hexdigest()
                        + "  joplin-md-sync-linux-arm64\n"
                    ).encode()
                else:
                    data = candidate
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ReleasePeer)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = f"http://127.0.0.1:{server.server_port}"
        self.bundle_url_template = origin + "/bundle/refs/{ref}"
        self.env = {
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("JOPLIN_", "XDG_"))
            },
            "HOME": str(self.home),
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "WIZARD_FIXTURE_ROOT": str(self.root),
            "JOPLIN_MD_SYNC_RELEASES_URL": origin,
            "JOPLIN_MD_SYNC_RELEASE_API_URL": origin + "/latest",
            "JOPLIN_TERMINAL_ASSET_BASE_URL": origin + "/bundle",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.profile = self.home / ".local/share/joplin-agent/profile"
        self.config = self.home / ".config/joplin-md-sync"
        self.unit = self.home / ".config/systemd/user/joplin-md-sync.service"

    def run_installer(self, *arguments, answers=(), remote=False, expected=0):
        command = [sys.executable, "-" if remote else str(INSTALLER), *arguments]
        record = self.root / "temporary-bundle-path"
        program = (
            "import sys\nfrom pathlib import Path\n"
            "def audit(event, args):\n"
            " if event == 'tempfile.mkdtemp' and Path(args[0]).name.startswith('joplin-installer-'):\n"
            f"  Path({str(record)!r}).write_text(args[0])\n"
            "sys.addaudithook(audit)\n"
            f"exec(compile({INSTALLER.read_bytes()!r}, '<stdin>', 'exec'), "
            "{'__name__': '__main__', '__file__': '<stdin>'})\n"
        )
        if not answers:
            result = subprocess.run(
                command,
                env=self.env,
                **({"input": program} if remote else {"stdin": subprocess.DEVNULL}),
                capture_output=True,
                text=True,
                timeout=45,
            )
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, expected, output)
        else:
            master, slave = pty.openpty()
            read_fd, write_fd = os.pipe() if remote else (slave, None)
            process = subprocess.Popen(
                command,
                env=self.env,
                stdin=read_fd,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                preexec_fn=lambda: fcntl.ioctl(slave, termios.TIOCSCTTY, 0),
            )
            if remote:
                os.close(read_fd)
                with os.fdopen(write_fd, "w") as stream:
                    stream.write(program)
            os.close(slave)
            received = bytearray()
            pending = list(answers)
            cursor = 0
            deadline = time.monotonic() + 45
            try:
                while process.poll() is None:
                    if select.select([master], [], [], 0.1)[0]:
                        try:
                            received.extend(os.read(master, 65536))
                        except OSError:
                            break
                    if pending:
                        label, answer = pending[0]
                        position = received.find(label.encode(), cursor)
                        if position >= 0:
                            os.write(master, (answer + "\n").encode())
                            cursor = position + len(label)
                            pending.pop(0)
                    self.assertLess(time.monotonic(), deadline, received.decode(errors="replace"))
                process.wait(timeout=5)
                output = received.decode(errors="replace")
                self.assertEqual(process.returncode, expected, output)
                self.assertFalse(pending, output)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                os.close(master)
        self.assertNotIn(SYNC_SECRET, output)
        self.assertNotIn(OAUTH_SECRET, output)
        self.assertNotIn(REPLACEMENT_SECRET, output)
        if remote:
            self.assertTrue(record.is_file())
            cache = Path(record.read_text())
            self.assertEqual(cache.parent, Path("/tmp"))
            self.assertFalse(cache.exists(), "temporary bundle was not removed")
        return output

    @staticmethod
    def public_answers(target="none", *target_answers):
        return [
            ("Installation prefix", ""),
            ("Joplin profile directory", ""),
            ("Joplin version (or latest)", ""),
            ("joplin-md-sync version (or latest)", ""),
            ("Local Joplin API port", ""),
            ("MCP/Actions port", ""),
            ("Sync interval in seconds", ""),
            ("Sync backend", target),
            *target_answers,
            ("Allow remote MCP/Actions connections?", ""),
            ("Enable GitHub OAuth for Claude?", ""),
            ("Enable user services after logout?", ""),
            ("Enable automatic service startup?", ""),
            ("Start services after setup?", ""),
        ]

    @classmethod
    def recovery_answers(cls, *sync_answers):
        """Setup questions when an existing profile supplies the sync defaults."""
        answers = [answer for answer in cls.public_answers() if answer[0] != "Sync backend"]
        return answers + list(sync_answers)

    def remove_units(self):
        """Leave the profile of an interrupted setup without its service pair."""
        for unit in (self.unit, self.unit.with_name("joplin-terminal.service")):
            unit.unlink()

    def install_nextcloud(self, *, remote=False):
        answers = self.public_answers(
            "nextcloud",
            ("Nextcloud WebDAV URL", "https://cloud.example.invalid/Joplin"),
            ("Nextcloud username", "fixture-user"),
        )
        answers.append(("Nextcloud password:", SYNC_SECRET))
        self.run_installer(answers=answers, remote=remote)

    def settings(self):
        return json.loads((self.profile / "fixture-settings.json").read_text())

    def register_oauth_client(self):
        """Exercise the installed candidate's real OAuth HTTP/storage runtime."""
        command = shlex.split(
            next(
                line.removeprefix("ExecStart=")
                for line in self.unit.read_text().splitlines()
                if line.startswith("ExecStart=")
            ).replace("%%", "%")
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        command[command.index("--mcp-port") + 1] = str(port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with (self.root / "oauth-runtime.log").open("ab") as output:
            server = subprocess.Popen(command, env=self.env, stdout=output, stderr=output)
            try:
                deadline = time.monotonic() + 20
                while True:
                    self.assertIsNone(server.poll())
                    try:
                        with opener.open(
                            f"http://127.0.0.1:{port}/healthz", timeout=0.2
                        ) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.03)
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/register",
                    data=json.dumps(
                        {
                            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                            "token_endpoint_auth_method": "none",
                        }
                    ).encode(),
                    headers={"Host": "notes.example.invalid", "Content-Type": "application/json"},
                )
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 201)
            finally:
                server.terminate()
                server.wait(timeout=10)

    def test_zero_argument_install_and_update_preserve_sync_without_any_prompt(self):
        self.install_nextcloud(remote=True)
        before = self.settings()
        self.assertEqual(before["sync.5.password"], SYNC_SECRET)
        calls = self.root / "calls.jsonl"
        calls.write_text("")
        self.run_installer()
        self.assertEqual(self.settings(), before)
        self.assertNotIn('"--export"', calls.read_text())
        self.assertNotIn(SYNC_SECRET, calls.read_text())
        self.assertTrue(self.unit.is_file())
        self.assertNotIn("/tmp/joplin-installer-", self.unit.read_text())

    def test_remote_bundle_tampering_fails_before_setup_and_removes_temporary_files(self):
        self.corrupt_bundle = True
        output = self.run_installer("--help", remote=True, expected=1)
        self.assertIn("checksum mismatch", output)
        self.assertFalse(list(self.home.iterdir()))

    def test_local_candidate_release_can_be_installed_before_publication(self):
        root = self.root / "candidate release"
        release = root / "download" / ("v" + self.version)
        release.mkdir(parents=True)
        checksum = hashlib.sha256(self.candidate).hexdigest()
        assets = ("joplin-md-sync-linux-amd64", "joplin-md-sync-linux-arm64")
        for asset in assets:
            (release / asset).write_bytes(self.candidate)
        (release / "SHA256SUMS.txt").write_text(
            "".join(f"{checksum}  {asset}\n" for asset in assets)
        )
        self.env["JOPLIN_MD_SYNC_RELEASES_URL"] = root.as_uri()
        self.run_installer("--non-interactive", "--joplin-md-sync-version", self.version)
        self.assertEqual((self.home / ".local/bin/joplin-md-sync").read_bytes(), self.candidate)

    def test_remote_branch_tag_and_commit_selection_never_falls_back_to_main(self):
        self.env["JOPLIN_TERMINAL_ASSET_BASE_URL"] = self.bundle_url_template
        self.env["JOPLIN_TERMINAL_INSTALLER_REF"] = "main"
        for ref in ("feat/guided-setup", "v" + self.version, "a" * 40):
            with self.subTest(ref=ref):
                self.bundle_requests.clear()
                output = self.run_installer("--installer-ref", ref, "--help", remote=True)
                self.assertIn("--installer-ref", output)
                self.assertTrue(self.bundle_requests)
                self.assertEqual(
                    {requested_ref for requested_ref, _file in self.bundle_requests}, {ref}
                )
                self.assertIn((ref, "installer-bundle.json"), self.bundle_requests)

    def test_remote_ref_environment_and_main_default_select_the_entire_bundle(self):
        self.env["JOPLIN_TERMINAL_ASSET_BASE_URL"] = self.bundle_url_template
        for ref in ("main", "feat/from-environment"):
            with self.subTest(ref=ref):
                self.bundle_requests.clear()
                if ref == "main":
                    self.env.pop("JOPLIN_TERMINAL_INSTALLER_REF", None)
                else:
                    self.env["JOPLIN_TERMINAL_INSTALLER_REF"] = ref
                self.run_installer("--help", remote=True)
                self.assertEqual(
                    {requested_ref for requested_ref, _file in self.bundle_requests}, {ref}
                )

    def test_explicit_ref_overrides_a_preexisting_raw_github_base_ref(self):
        namespace = runpy.run_path(str(INSTALLER))
        environment = {
            "JOPLIN_TERMINAL_INSTALLER_REF": "main",
            "JOPLIN_TERMINAL_ASSET_BASE_URL": "https://raw.githubusercontent.com/example/fork/main/scripts/joplin_terminal_service",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            ref, base, explicit = namespace["source_selection"](["--ref", "feat/test-setup"])
        self.assertEqual(ref, "feat/test-setup")
        self.assertEqual(
            base,
            "https://raw.githubusercontent.com/example/fork/feat%2Ftest-setup/scripts/joplin_terminal_service",
        )
        self.assertTrue(explicit)

    def test_zero_argument_defaults_and_cli_shortcuts_choose_the_same_configuration(self):
        self.run_installer(answers=self.public_answers())
        before = self.settings()
        self.assertEqual(before["sync.target"], 0)
        self.run_installer("--non-interactive", "--sync-target", "none", "--no-start-service")
        self.assertEqual(self.settings(), before)
        self.run_installer("--clean")
        self.run_installer("--non-interactive")
        self.assertEqual(self.settings(), before)

    def test_dry_run_uses_the_same_disabled_startup_defaults_as_installation(self):
        output = self.run_installer("--sync-target", "none", "--non-interactive", "--dry-run")
        self.assertNotIn("would enable joplin", output)
        self.assertNotIn("would restart both services", output)
        self.assertFalse(list(self.home.iterdir()))
        self.run_installer("--non-interactive")
        choices = json.loads((self.config / "installer-settings.json").read_text())["choices"]
        self.assertTrue(choices["no_start_service"])
        self.assertTrue(choices["no_enable_service"])

    def test_explicit_unchanged_sync_reuses_its_password_and_changed_endpoint_requires_a_new_one(
        self,
    ):
        self.install_nextcloud()
        before = self.settings()
        self.run_installer("--sync-target", "nextcloud", "--non-interactive")
        self.assertEqual(self.settings(), before)
        username = self.root / "username"
        username.write_text("fixture-user\n")
        username.chmod(0o600)
        self.run_installer("--sync-username-file", str(username), "--non-interactive")
        self.assertEqual(self.settings(), before)
        output = self.run_installer(
            "--sync-location",
            "https://other.example.invalid/Joplin",
            "--non-interactive",
            expected=1,
        )
        self.assertIn("--sync-secret-file", output)
        self.assertEqual(self.settings(), before)
        self.run_installer(
            "--sync-location",
            "https://other.example.invalid/Joplin",
            answers=[("Nextcloud password:", REPLACEMENT_SECRET)],
        )
        self.assertEqual(self.settings()["sync.5.password"], REPLACEMENT_SECRET)
        self.assertEqual(self.settings()["sync.5.path"], "https://other.example.invalid/Joplin")

    def test_persisted_browser_and_saml_authentication_is_reused_without_a_terminal(self):
        self.run_installer(answers=self.public_answers())
        launcher = self.home / ".local/bin/joplin"
        for target, settings in (
            ("onedrive", {"sync.target": 3, "sync.3.auth": "cached-fixture-auth"}),
            ("joplin-cloud", {"sync.target": 10, "sync.10.password": "cached-fixture-auth"}),
            (
                "joplin-server-saml",
                {
                    "sync.target": 11,
                    "sync.11.path": "https://saml.example.invalid",
                    "sync.11.id": "cached-session",
                    "sync.11.userId": "cached-user",
                },
            ),
        ):
            with self.subTest(target=target):
                imported = subprocess.run(
                    [str(launcher), "--profile", str(self.profile), "config", "--import"],
                    input=json.dumps(settings),
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(imported.returncode, 0)
                self.run_installer("--sync-target", target, "--non-interactive")
                for key, value in settings.items():
                    self.assertEqual(self.settings()[key], value)

    def test_changed_account_name_never_receives_the_previous_password(self):
        self.install_nextcloud()
        before = self.settings()
        output = self.run_installer(
            "--sync-username", "fixture-user/", "--non-interactive", expected=1
        )
        self.assertIn("--sync-secret-file", output)
        self.assertEqual(self.settings(), before)

    def test_reconfigure_existing_profile_without_units_clears_unused_credentials(self):
        self.install_nextcloud()
        self.unit.unlink()
        self.unit.with_name("joplin-terminal.service").unlink()
        self.run_installer("--reconfigure", answers=self.public_answers())
        self.assertEqual(self.settings()["sync.target"], 0)
        self.assertEqual(self.settings()["sync.5.password"], "")
        self.assertEqual(self.settings()["sync.5.path"], "")

    def test_existing_nextcloud_profile_without_units_keeps_sync_when_defaults_are_accepted(self):
        self.install_nextcloud()
        before = self.settings()
        self.remove_units()
        output = self.run_installer(answers=self.recovery_answers())
        self.assertEqual(self.settings(), before)
        self.assertNotIn("Sync backend", output)
        self.assertNotIn("Nextcloud password", output)
        self.assertTrue(self.unit.is_file())
        # Automation recovers the same profile without --reconfigure or sync options.
        self.remove_units()
        self.run_installer("--non-interactive")
        self.assertEqual(self.settings(), before)
        self.assertTrue(self.unit.is_file())

    def test_existing_filesystem_profile_without_units_keeps_target_and_sandbox_path(self):
        directory = self.root / "filesystem sync"
        directory.mkdir()
        self.run_installer(
            answers=self.public_answers("filesystem", ("absolute directory", str(directory)))
        )
        before = self.settings()
        self.assertEqual(before["sync.target"], 2)
        backend = self.unit.with_name("joplin-terminal.service")
        original_backend = backend.read_text()
        self.assertIn(str(directory), original_backend)
        self.remove_units()
        output = self.run_installer(answers=self.recovery_answers())
        self.assertNotIn("Sync backend", output)
        self.assertNotIn("absolute directory", output)
        self.assertEqual(self.settings(), before)
        self.assertEqual(backend.read_text(), original_backend)

    def test_empty_or_unconfigured_profile_without_units_still_asks_for_the_sync_backend(self):
        self.profile.mkdir(mode=0o700, parents=True)
        # An empty directory is a fresh profile: the normal question order applies.
        self.run_installer(answers=self.public_answers())
        before = self.settings()
        self.assertEqual(before["sync.target"], 0)
        self.remove_units()
        # A readable profile without a configured target still offers the choice.
        self.run_installer(answers=self.recovery_answers(("Sync backend", "")))
        self.assertEqual(self.settings(), before)

    def test_existing_profile_without_units_applies_an_explicit_sync_target_change(self):
        self.install_nextcloud()
        self.remove_units()
        directory = self.root / "replacement filesystem sync"
        directory.mkdir()
        self.run_installer(
            "--sync-target", "filesystem", "--sync-location", str(directory), "--non-interactive"
        )
        after = self.settings()
        self.assertEqual(after["sync.target"], 2)
        self.assertEqual(after["sync.2.path"], str(directory))
        self.assertEqual(after["sync.5.password"], "")
        self.assertEqual(after["sync.5.path"], "")

    def test_partial_service_pair_is_rejected_before_prompts_or_changes(self):
        self.install_nextcloud()
        before = self.settings()
        original = self.unit.read_bytes()
        self.unit.with_name("joplin-terminal.service").unlink()
        output = self.run_installer(expected=1)
        self.assertIn("incomplete installed service pair", output)
        self.assertEqual(self.settings(), before)
        self.assertEqual(self.unit.read_bytes(), original)
        self.run_installer("--clean", expected=1)
        self.assertEqual(self.unit.read_bytes(), original)
        self.run_installer(
            "--clean",
            "--profile-dir",
            str(self.profile),
            "--joplin-prefix",
            str(self.home / ".local"),
        )
        self.assertFalse(self.unit.exists())

    def test_inconsistent_service_credential_path_is_rejected_before_service_changes(self):
        self.install_nextcloud()
        before = self.settings()
        changed = self.unit.read_text().replace(
            str(self.config / "mcp-token"), str(self.root / "external-mcp-token")
        )
        self.unit.write_text(changed)
        calls = self.root / "calls.jsonl"
        calls.write_text("")
        output = self.run_installer(expected=1)
        self.assertIn("installed service paths disagree", output)
        self.assertEqual(self.unit.read_text(), changed)
        self.assertEqual(self.settings(), before)
        self.assertEqual(calls.read_text(), "")
        output = self.run_installer("--clean", expected=1)
        self.assertIn("cannot recover installed service paths", output)
        self.assertEqual(self.settings(), before)
        self.assertEqual(self.unit.read_text(), changed)
        self.assertEqual(calls.read_text(), "")

    def test_partial_oauth_flags_prompt_only_missing_values_and_reconfigure_removes_owned_secrets(
        self,
    ):
        self.install_nextcloud()
        before = self.settings()
        self.run_installer(
            "--github-oauth",
            "--github-oauth-client-id",
            "fixture-client",
            "--no-start-service",
            answers=[
                ("Public OAuth HTTPS origin", "https://notes.example.invalid"),
                ("Allowed numeric GitHub user ID", "42"),
                ("GitHub login hint", ""),
                ("OAuth state directory", ""),
                ("OAuth access token lifetime", ""),
                ("GitHub Client Secret file", ""),
                ("GitHub Client Secret:", OAUTH_SECRET),
                ("OAuth signing key file", ""),
            ],
        )
        self.assertEqual(self.settings(), before)
        self.assertIn("--github-oauth", self.unit.read_text())
        secrets_dir = self.config / "oauth-secrets"
        self.assertEqual(len(list(secrets_dir.iterdir())), 2)
        for file in secrets_dir.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.run_installer()
        self.assertEqual(self.settings(), before)
        self.assertNotIn(OAUTH_SECRET, self.unit.read_text())
        self.assertNotIn(OAUTH_SECRET, (self.config / "oauth-settings.json").read_text())
        owned_files = set(secrets_dir.iterdir())
        self.run_installer("--no-github-oauth")
        self.assertNotIn("--github-oauth", self.unit.read_text())
        self.assertTrue(all(file.exists() for file in owned_files))
        self.run_installer("--github-oauth")
        self.assertIn("--github-oauth", self.unit.read_text())
        self.assertEqual(set(secrets_dir.iterdir()), owned_files)
        self.run_installer("--reconfigure", answers=self.public_answers())
        after = self.settings()
        self.assertEqual(after["sync.target"], 0)
        self.assertEqual(after["sync.5.password"], "")
        self.assertEqual(after["sync.5.path"], "")
        self.assertNotIn("--github-oauth", self.unit.read_text())
        self.assertFalse(secrets_dir.exists())
        self.assertFalse((self.config / "oauth-settings.json").exists())

    def test_reconfiguration_preserves_external_oauth_credentials_and_custom_state(self):
        self.run_installer(answers=self.public_answers())
        secret = self.root / "operator-client-secret"
        descriptor = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(OAUTH_SECRET + "\n")
        custom = self.root / "operator-custom-state"
        self.run_installer(
            "--github-oauth",
            "--github-oauth-client-id",
            "fixture-client",
            "--github-oauth-public-url",
            "https://notes.example.invalid",
            "--github-oauth-allowed-user-id",
            "42",
            "--github-oauth-client-secret-file",
            str(secret),
            "--github-oauth-state-dir",
            str(custom),
            "--non-interactive",
        )
        sentinel = custom / "operator-file"
        sentinel.write_text("preserve")
        self.run_installer("--reconfigure", answers=self.public_answers())
        self.assertTrue(secret.is_file())
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assertNotIn("--github-oauth", self.unit.read_text())

    def test_reconfiguration_with_oauth_enabled_replaces_old_owned_keys_and_grants(self):
        self.run_installer(answers=self.public_answers())
        secret = self.root / "operator-client-secret"
        descriptor = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(OAUTH_SECRET + "\n")
        options = [
            "--github-oauth",
            "--github-oauth-client-id",
            "fixture-client",
            "--github-oauth-public-url",
            "https://notes.example.invalid",
            "--github-oauth-allowed-user-id",
            "42",
            "--github-oauth-client-secret-file",
            str(secret),
            "--non-interactive",
        ]
        self.run_installer(*options)
        self.register_oauth_client()
        state = self.home / ".local/state/joplin-md-sync/oauth"
        self.assertTrue(list((state / "store").rglob("*.json")))
        old_keys = set((self.config / "oauth-secrets").iterdir())
        self.run_installer("--reconfigure", *options)
        self.assertFalse(any(key.exists() for key in old_keys))
        self.assertEqual(list(state.iterdir()), [])
        self.assertTrue((self.config / "oauth-managed-state.json").is_file())
        self.register_oauth_client()
        self.assertTrue(list((state / "store").rglob("*.json")))

    def enable_oauth_non_interactively(self):
        secret = self.root / "operator-client-secret"
        if not secret.exists():
            descriptor = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(OAUTH_SECRET + "\n")
        self.run_installer(
            "--github-oauth",
            "--github-oauth-client-id",
            "fixture-client",
            "--github-oauth-public-url",
            "https://notes.example.invalid",
            "--github-oauth-allowed-user-id",
            "42",
            "--github-oauth-client-secret-file",
            str(secret),
            "--non-interactive",
        )
        return self.unit.read_text()

    def oauth_files(self):
        """Saved OAuth settings, owned secrets, the state registration and grants."""
        state = self.home / ".local/state/joplin-md-sync/oauth"
        paths = [
            self.config / "oauth-settings.json",
            self.config / "oauth-managed-state.json",
            *(self.config / "oauth-secrets").rglob("*"),
            *state.rglob("*"),
        ]
        return {path: path.read_bytes() for path in paths if path.is_file()}

    def test_negative_oauth_environment_disables_like_no_github_oauth_and_reenables(self):
        self.run_installer("--non-interactive")
        enabled_unit = self.enable_oauth_non_interactively()
        self.register_oauth_client()
        state = self.home / ".local/state/joplin-md-sync/oauth"
        self.assertIn(f'ReadWritePaths="{state}"', enabled_unit)
        preserved = self.oauth_files()
        self.assertTrue(any(path.suffix == ".json" and state in path.parents for path in preserved))
        self.assertTrue(list((self.config / "oauth-secrets").iterdir()))
        for value in ("false", "0", "NO"):
            with self.subTest(value=value):
                self.env["JOPLIN_GITHUB_OAUTH"] = value
                self.run_installer()
                disabled_unit = self.unit.read_text()
                self.assertNotIn("--github-oauth", disabled_unit)
                self.assertNotIn(str(state), disabled_unit)
                self.assertEqual(self.oauth_files(), preserved)
                # Re-enabling reuses saved settings, secrets and grants without questions.
                self.env["JOPLIN_GITHUB_OAUTH"] = "yes"
                self.run_installer()
                self.assertEqual(self.unit.read_text(), enabled_unit)
                self.assertEqual(self.oauth_files(), preserved)
        self.env.pop("JOPLIN_GITHUB_OAUTH")
        self.run_installer("--no-github-oauth")
        self.assertEqual(self.unit.read_text(), disabled_unit)
        self.assertEqual(self.oauth_files(), preserved)

    def test_command_line_oauth_choice_overrides_the_environment_and_invalid_value_fails(self):
        self.run_installer("--non-interactive")
        enabled_unit = self.enable_oauth_non_interactively()
        preserved = self.oauth_files()
        self.env["JOPLIN_GITHUB_OAUTH"] = "true"
        self.run_installer("--no-github-oauth")
        self.assertNotIn("--github-oauth", self.unit.read_text())
        self.assertEqual(self.oauth_files(), preserved)
        self.env["JOPLIN_GITHUB_OAUTH"] = "false"
        self.run_installer("--github-oauth")
        self.assertEqual(self.unit.read_text(), enabled_unit)
        self.assertEqual(self.oauth_files(), preserved)
        calls = self.root / "calls.jsonl"
        for value, arguments, message in (
            ("false", ("--github-oauth", "--no-github-oauth"), "cannot be combined"),
            ("maybe", (), "JOPLIN_GITHUB_OAUTH must be one of"),
        ):
            with self.subTest(value=value, arguments=arguments):
                self.env["JOPLIN_GITHUB_OAUTH"] = value
                calls.write_text("")
                output = self.run_installer(*arguments, expected=1)
                self.assertIn(message, output)
                self.assertEqual(calls.read_text(), "")
                self.assertEqual(self.unit.read_text(), enabled_unit)
                self.assertEqual(self.oauth_files(), preserved)

    def test_clean_never_prompts_and_removes_only_the_managed_installation(self):
        self.run_installer(answers=self.public_answers())
        unrelated = self.home / "keep-me"
        unrelated.write_text("unrelated")
        self.run_installer("--clean")
        self.assertFalse(self.profile.exists())
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.config.exists())
        self.assertTrue(unrelated.is_file())
        self.run_installer("--clean")

    def test_clean_removes_only_the_adapter_executable_recorded_by_the_installer(self):
        binary = self.home / ".local/bin/joplin-md-sync"
        receipt = self.home / ".local/state/joplin-agent/joplin-md-sync-receipt.json"
        self.run_installer("--non-interactive")
        self.assertEqual(
            json.loads(receipt.read_text()),
            {
                "schema": 1,
                "path": str(binary),
                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            },
        )
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.run_installer("--clean")
        self.assertFalse(binary.exists())
        # An executable the installer accepted but did not place stays after clean.
        binary.write_bytes(self.candidate)
        binary.chmod(0o700)
        self.run_installer("--non-interactive")
        self.assertFalse(receipt.exists())
        output = self.run_installer("--clean")
        self.assertIn(f"preserving {binary}", output)
        self.assertEqual(binary.read_bytes(), self.candidate)
        self.assertFalse(self.profile.exists())
        self.assertFalse(self.unit.exists())

    def test_clean_preserves_operator_oauth_secrets_in_the_shared_config_directory(self):
        self.run_installer("--non-interactive")
        # The connector guide places operator-created secrets in this directory.
        operator_files = {
            self.config / "github-client-secret": OAUTH_SECRET,
            self.config / "oauth-signing-key": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        }
        for path, value in operator_files.items():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(value + "\n")
        self.run_installer(
            "--github-oauth",
            "--github-oauth-client-id",
            "fixture-client",
            "--github-oauth-public-url",
            "https://notes.example.invalid",
            "--github-oauth-allowed-user-id",
            "42",
            "--github-oauth-client-secret-file",
            str(self.config / "github-client-secret"),
            "--github-oauth-signing-key-file",
            str(self.config / "oauth-signing-key"),
            "--non-interactive",
        )
        state = self.home / ".local/state/joplin-md-sync/oauth"
        self.assertTrue(state.is_dir())
        self.assertTrue((self.config / "oauth-settings.json").is_file())
        self.run_installer("--clean")
        self.assertFalse(self.unit.exists())
        self.assertFalse(state.exists())
        self.assertEqual(set(self.config.iterdir()), set(operator_files))
        for path, value in operator_files.items():
            self.assertEqual(path.read_text(), value + "\n")

    def test_update_without_saved_choices_can_be_repeated(self):
        self.run_installer("--non-interactive")
        # Installations from earlier installer releases have no saved choices.
        (self.config / "installer-settings.json").unlink()
        self.run_installer("--no-start-service")
        choices = json.loads((self.config / "installer-settings.json").read_text())["choices"]
        self.assertNotIn(None, choices.values())
        self.run_installer("--no-start-service")

    def test_malformed_bundle_url_fails_without_a_traceback(self):
        self.env["JOPLIN_TERMINAL_ASSET_BASE_URL"] = "bundle.example.invalid/{ref}"
        output = self.run_installer("--help", remote=True, expected=1)
        self.assertIn("could not download the installer bundle", output)
        self.assertNotIn("Traceback", output)
        self.assertFalse(list(self.home.iterdir()))


if __name__ == "__main__":
    unittest.main()
