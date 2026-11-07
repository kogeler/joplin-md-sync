"""Executable dependency/crypto/metadata/resource and GitHub TLS client probe.

The E0 gate checks dependencies, encrypted storage, metadata and templates.
``--tls-client`` runs only the packaged GitHub HTTPS client check; the smoke
harness runs it inside each built runtime. Imports are deferred so that
``--import-path`` can put a zipapp first on ``sys.path``. All credentials and
certificates below are local test fixtures.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import ipaddress
import json
import os
import ssl
import sys
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


async def probe() -> None:
    from importlib.metadata import version

    from fastmcp import FastMCP
    from fastmcp.server.auth.oauth_proxy.ui import create_consent_html
    from key_value.aio.stores.filetree import FileTreeStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    from joplin_md_sync.github_oauth import CLAUDE_CALLBACK, OwnerGitHubOAuth
    from joplin_md_sync.oauth_http import BoundedH11Protocol

    with tempfile.TemporaryDirectory(prefix="jms-oauth-probe-") as directory:

        def store() -> FernetEncryptionWrapper:
            return FernetEncryptionWrapper(
                FileTreeStore(data_directory=Path(directory)),
                source_material="packaging-fixture-key-with-32-bytes",
                salt="jms-oauth-packaging-probe",
                raise_on_decryption_error=True,
            )

        first = store()
        canary = "packaging-fixture-token-plaintext-canary"
        await first.put(key="probe", value={"token": canary}, ttl=60)
        assert await store().get(key="probe") == {"token": canary}
        for path in Path(directory).rglob("*"):
            if path.is_file():
                assert canary.encode() not in path.read_bytes()

        auth = OwnerGitHubOAuth(
            client_id="packaging-fixture-client",
            client_secret="packaging-fixture-secret",
            user_id="42",
            base_url="https://joplin.example.invalid",
            signing_key=b"packaging-fixture-key-with-32-bytes",
            storage=first,
        )
        app = FastMCP("Packaging fixture", auth=auth).http_app(
            path="/claude/mcp", stateless_http=True, json_response=True
        )
        paths = {getattr(route, "path", "") for route in app.routes}
        assert {"/authorize", "/token", "/register", "/auth/callback", "/claude/mcp"} <= paths
        consent = create_consent_html(
            client_id="probe",
            redirect_uri=CLAUDE_CALLBACK,
            scopes=["read:user"],
            txn_id="fixture",
            csrf_token="fixture",
            client_name="<script>fixture</script>",
        )
        assert "&lt;script&gt;fixture&lt;/script&gt;" in consent
        assert "Content-Security-Policy" in consent
        assert BoundedH11Protocol.max_connections > 0
        print(json.dumps({"ok": True, "fastmcp": version("fastmcp"), "mcp": version("mcp")}))


def fixture_authority(name: str):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def server_material(directory: Path, name: str) -> tuple[bytes, Path, Path]:
    """Return a new CA's PEM and a localhost certificate/key signed by it."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    authority_key, authority = fixture_authority(name)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(authority.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()),
            critical=False,
        )
        .sign(authority_key, hashes.SHA256())
    )
    certificate_path, key_path = directory / f"{name}.pem", directory / f"{name}.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return authority.public_bytes(serialization.Encoding.PEM), certificate_path, key_path


@contextlib.contextmanager
def https_server(certificate: Path, key: Path, hits: list[str]) -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/followed")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"packaging-tls-fixture"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            # A rejected certificate aborts the client side of the connection.
            pass

    server = Server(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def certificate_rejected(error: BaseException) -> bool:
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, ssl.SSLCertVerificationError):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


async def tls_client_probe() -> None:
    """Exercise the production GitHub client against a probe-only test CA.

    The CA is added only here, to the production client's own SSL context. An
    ambient CA file and proxy are set to prove that ``trust_env=False`` ignores
    them; the client never disables verification and follows no redirect.
    """
    import httpx2
    import truststore

    import joplin_md_sync
    from joplin_md_sync.github_oauth import github_http_client

    with tempfile.TemporaryDirectory(prefix="jms-tls-probe-") as temporary:
        directory = Path(temporary)
        trusted, certificate, key = server_material(directory, "probe-trusted-ca")
        _, foreign_certificate, foreign_key = server_material(directory, "probe-foreign-ca")
        ambient = directory / "ambient-ca.pem"
        ambient.write_bytes(trusted)
        saved = {name: os.environ.get(name) for name in ("SSL_CERT_FILE", "HTTPS_PROXY")}
        os.environ.update(SSL_CERT_FILE=str(ambient), HTTPS_PROXY="http://127.0.0.1:9")
        try:
            client = github_http_client()
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        assert client.trust_env is False and client.follow_redirects is False
        context = client._transport._pool._ssl_context
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        if isinstance(context, truststore.SSLContext):
            trust, anchors = "truststore", None
        else:
            trust, anchors = "openssl", context.cert_store_stats()["x509_ca"]
            capath = ssl.get_default_verify_paths().openssl_capath
            assert anchors or os.path.isdir(capath), "Production client has no OS trust anchors"
        hits: list[str] = []
        async with client:
            with (
                https_server(certificate, key, hits) as port,
                https_server(foreign_certificate, foreign_key, hits) as foreign,
            ):
                url = f"https://localhost:{port}"
                try:
                    await client.get(url + "/probe")
                except httpx2.ConnectError as error:
                    assert certificate_rejected(error), error
                else:
                    raise AssertionError("An ambient or unknown CA was trusted")
                context.load_verify_locations(cadata=trusted.decode("ascii"))
                response = await client.get(url + "/probe")
                assert response.status_code == 200
                assert response.content == b"packaging-tls-fixture"
                redirect = await client.get(url + "/redirect")
                assert redirect.status_code == 302 and "/followed" not in hits
                try:
                    await client.get(f"https://localhost:{foreign}/probe")
                except httpx2.ConnectError as error:
                    assert certificate_rejected(error), error
                else:
                    raise AssertionError("A certificate from an untrusted CA was accepted")
        assert hits == ["/probe", "/redirect"]
    print(
        json.dumps(
            {
                "tls_client": "ok",
                "trust": trust,
                "os_trust_anchors": anchors,
                "frozen": bool(getattr(sys, "frozen", False)),
                "module": joplin_md_sync.__file__,
                "prefix": sys.prefix,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tls-client", action="store_true", help="run only the GitHub TLS client probe"
    )
    parser.add_argument(
        "--import-path", help="put this zipapp or directory first on the import path"
    )
    args = parser.parse_args()
    if args.import_path:
        sys.path.insert(0, args.import_path)
    if not args.tls_client:
        asyncio.run(probe())
    asyncio.run(tls_client_probe())


if __name__ == "__main__":
    main()
