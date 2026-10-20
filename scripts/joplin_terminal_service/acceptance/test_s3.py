"""Real Joplin CLI against an isolated, signature-verifying S3 protocol fixture.

No operator profile, cloud account, systemd, or external network is used at run
time. This fixture implements only the S3 operations exercised by Joplin, not
a claim of full compatibility with every S3 provider.
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_joplin_terminal as installer

# Public synthetic fixture values, never real cloud credentials.
ACCESS_KEY = "fixture-access-key"
SECRET_KEY = 'fixture-secret-with-quotes-"-and-unicode-λ'
BUCKET = "isolated-joplin-notes"
REGION = "custom-region-1"


class S3Fixture(http.server.ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), S3Handler)
        self.objects: dict[str, bytes] = {}
        self.requests: list[tuple[str, str]] = []
        self.rejected = 0
        self.rejection_reason = ""


class S3Handler(http.server.BaseHTTPRequestHandler):
    server: S3Fixture
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: object) -> None:
        pass

    def respond(self, status: int, data: bytes = b"", **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Type", "application/xml")
        self.send_header("ETag", '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"')
        self.send_header("Last-Modified", "Tue, 20 Oct 2026 00:00:00 GMT")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def authenticated(self, body: bytes) -> bool:
        """Verify the real AWS SDK signature, including endpoint, region and key."""
        try:
            url = urllib.parse.urlsplit(self.path)
            query_fields = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
            if "X-Amz-Signature" in query_fields:
                algorithm = query_fields["X-Amz-Algorithm"]
                params = {
                    name: query_fields[f"X-Amz-{name}"]
                    for name in ("Credential", "SignedHeaders", "Signature")
                }
                date = query_fields["X-Amz-Date"]
                payload = "UNSIGNED-PAYLOAD"
            else:
                algorithm, fields = self.headers["Authorization"].split(" ", 1)
                params = dict(part.strip().split("=", 1) for part in fields.split(","))
                date = self.headers["x-amz-date"]
                payload = self.headers.get("x-amz-content-sha256", hashlib.sha256(body).hexdigest())
            access_key, day, region, service, terminator = params["Credential"].split("/")
            if (algorithm, access_key, region, service, terminator) != (
                "AWS4-HMAC-SHA256",
                ACCESS_KEY,
                REGION,
                "s3",
                "aws4_request",
            ):
                self.server.rejection_reason = "credential scope mismatch"
                return False
            query = "&".join(
                f"{urllib.parse.quote(key, safe='-_.~')}={urllib.parse.quote(value, safe='-_.~')}"
                for key, value in sorted(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
                if key != "X-Amz-Signature"
            )
            signed = params["SignedHeaders"]
            canonical_headers = "".join(
                f"{name}:{' '.join(self.headers[name].split())}\n" for name in signed.split(";")
            )
            canonical = "\n".join(
                (self.command, url.path, query, canonical_headers, signed, payload)
            )
            scope = f"{day}/{region}/s3/aws4_request"
            to_sign = "\n".join(
                (algorithm, date, scope, hashlib.sha256(canonical.encode()).hexdigest())
            )
            key = ("AWS4" + SECRET_KEY).encode()
            for component in (day, region, "s3", "aws4_request"):
                key = hmac.digest(key, component.encode(), "sha256")
            expected = hmac.new(key, to_sign.encode(), "sha256").hexdigest()
            valid = hmac.compare_digest(expected, params["Signature"])
            if not valid:
                self.server.rejection_reason = f"signature mismatch; canonical request: {canonical}"
            return valid
        except (KeyError, ValueError, AttributeError, TypeError) as exc:
            self.server.rejection_reason = f"malformed authorization: {type(exc).__name__}; fields: {list(self.headers.keys())}"
            return False

    def handle_request(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self.authenticated(body):
            self.server.rejected += 1
            self.respond(403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>")
            return
        url = urllib.parse.urlsplit(self.path)
        prefix = f"/custom-endpoint/{BUCKET}"
        if not (url.path == prefix or url.path.startswith(prefix + "/")):
            self.respond(404, b"<Error><Code>NoSuchBucket</Code></Error>")
            return
        key = urllib.parse.unquote(url.path[len(prefix) :].lstrip("/"))
        self.server.requests.append((self.command, key))
        if not key and self.command == "GET":
            query = urllib.parse.parse_qs(url.query)
            selected_prefix = query.get("prefix", [""])[0]
            contents = "".join(
                f"<Contents><Key>{escape(key)}</Key><LastModified>2026-10-20T00:00:00.000Z</LastModified>"
                f"<ETag>fixture</ETag><Size>{len(data)}</Size><StorageClass>STANDARD</StorageClass></Contents>"
                for key, data in sorted(self.server.objects.items())
                if key.startswith(selected_prefix)
            )
            self.respond(
                200,
                (
                    '<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    f"<Name>{BUCKET}</Name><Prefix>{escape(selected_prefix)}</Prefix>"
                    f"<IsTruncated>false</IsTruncated><KeyCount>{len(self.server.objects)}</KeyCount>"
                    f"{contents}</ListBucketResult>"
                ).encode(),
            )
        elif self.command == "PUT":
            self.server.objects[key] = body
            self.respond(200)
        elif self.command == "DELETE":
            self.server.objects.pop(key, None)
            self.respond(204)
        elif self.command in ("GET", "HEAD"):
            if key in self.server.objects:
                self.respond(200, self.server.objects[key])
            else:
                self.respond(404, b"<Error><Code>NoSuchKey</Code></Error>")
        else:
            self.respond(501)

    do_GET = handle_request
    do_HEAD = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request


class RealJoplinS3Tests(unittest.TestCase):
    def test_install_configure_sync_and_download_with_custom_endpoint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="joplin-s3-acceptance-") as temporary:
            root = Path(temporary)
            server = S3Fixture()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            for filename, value in (("access-key", ACCESS_KEY), ("secret-key", SECRET_KEY)):
                installer.atomic_write(root / filename, (value + "\n").encode(), 0o600)
            # The image build installs through the real installer, including
            # native npm modules. Reuse that tree for offline runtime tests.
            env = {**os.environ, "HOME": str(root)}
            args = installer.build_parser(env).parse_args(
                [
                    "--sync-target",
                    "s3",
                    "--sync-location",
                    BUCKET,
                    "--sync-username-file",
                    str(root / "access-key"),
                    "--sync-secret-file",
                    str(root / "secret-key"),
                    "--s3-region",
                    REGION,
                    "--s3-endpoint",
                    f"http://127.0.0.1:{server.server_port}/custom-endpoint/",
                    "--s3-force-path-style",
                    "--joplin-prefix",
                    str(root / "prefix"),
                    "--profile-dir",
                    str(root / "sender"),
                    "--non-interactive",
                ]
            )
            installer.validate_args(args)
            paths = installer.build_paths(args, env)
            shutil.copytree(os.environ["JOPLIN_TEST_PREFIX"], paths.prefix, symlinks=True)
            redactor = installer.SecretRedactor()
            runner = installer.CommandRunner(redactor, env)
            dependencies = installer.Dependencies(
                Path(shutil.which("node")),
                Path(shutil.which("npm")),
                Path("/usr/bin/systemctl"),
                "22",
                "10",
            )
            installer.install_or_update_joplin(
                runner, dependencies, paths, os.environ["JOPLIN_TEST_VERSION"]
            )
            configuration = installer.resolve_sync_configuration(args, redactor)
            installer.configure_profile(runner, paths, args, configuration)
            saved = json.loads(
                runner.run(installer.joplin_command(paths, "config", "--export")).stdout
            )
            self.assertEqual(saved["sync.8.username"], ACCESS_KEY)
            self.assertEqual(saved["sync.8.password"], SECRET_KEY)

            # These go through supported CLI commands, never DB/profile reads.
            def cli(*command: str) -> str:
                return runner.run(installer.joplin_command(paths, *command)).stdout

            cli("mkbook", "S3 acceptance")
            cli("use", "S3 acceptance")
            cli("mknote", "Round-trip sentinel")
            with mock.patch.object(installer, "SYNC_TIMEOUT", 90):
                installer.run_sync(runner, paths, "upload fixture note")
            self.assertEqual(server.rejected, 0, server.rejection_reason)
            self.assertIn("info.json", server.objects)
            self.assertTrue(
                any(method == "PUT" and key.endswith(".md") for method, key in server.requests)
            )
            args.profile_dir = str(root / "receiver")
            paths = installer.build_paths(args, env)
            installer.configure_profile(runner, paths, args, configuration)
            with mock.patch.object(installer, "SYNC_TIMEOUT", 90):
                installer.run_sync(runner, paths, "download fixture note")
            cli("use", "S3 acceptance")
            self.assertIn("Round-trip sentinel", cli("ls"))
            exported = json.loads(cli("config", "--export"))
            self.assertEqual(exported["sync.8.password"], SECRET_KEY)
            self.assertEqual(exported["sync.8.region"], REGION)
            self.assertTrue(exported["sync.8.forcePathStyle"])
            self.assertEqual(server.rejected, 0)
            installer.write_setting(
                runner, paths, "sync.8.password", "incorrect-fixture-key", secret=True
            )
            with (
                mock.patch.object(installer, "SYNC_TIMEOUT", 90),
                self.assertRaises(installer.ToolError),
            ):
                installer.run_sync(runner, paths, "reject wrong S3 secret")
            self.assertGreater(server.rejected, 0)
            installer.write_setting(runner, paths, "sync.8.password", SECRET_KEY, secret=True)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                api_port = listener.getsockname()[1]
            installer.write_setting(runner, paths, "api.port", str(api_port))
            supervisor = Path(__file__).resolve().parents[1] / "run_joplin_terminal.py"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(supervisor),
                    "--node-path",
                    str(dependencies.node),
                    "--joplin-path",
                    str(paths.launcher),
                    "--profile-dir",
                    str(paths.profile_dir),
                    "--lock-file",
                    str(paths.lock_file),
                    "--api-port",
                    str(api_port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=runner.env,
            )
            try:
                installer.wait_for_api(api_port, timeout=20)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertFalse(installer.ping_api(api_port, timeout=1).ok)


if __name__ == "__main__":
    unittest.main()
