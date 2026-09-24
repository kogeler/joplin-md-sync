"""Machine- and user-scoped Make environments for shared checkouts."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from scripts import venv_root

ROOT = Path(__file__).parents[2]
MAKEFILE = ROOT / "Makefile"
FIRST_ID = "0123456789abcdef" * 2
SECOND_ID = "fedcba9876543210" * 2
ENVIRONMENTS = (
    "VENV",
    "VENV_DEV",
    "VENV_DOCS",
    "VENV_LOCK",
    "VENV_PACKAGE",
    "VENV_SMOKE",
    "VENV_TEST",
)
make_integration = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("make") is None,
    reason="the Makefile integration fixture substitutes the Linux machine ID file",
)


def _environment_name(variable: str) -> str:
    return variable.lower().replace("_", "-")


def test_environment_key_is_stable_private_and_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    identities = {"machine": bytes.fromhex(FIRST_ID), "user": b"1000"}
    monkeypatch.setattr(venv_root, "machine_identity", lambda: identities["machine"])
    monkeypatch.setattr(venv_root, "user_identity", lambda: identities["user"])

    first = venv_root.environment_key()
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    assert venv_root.environment_key() == first
    assert venv_root.repository_venv_root() == PurePosixPath(".venvs", first)

    identities["machine"] = bytes.fromhex(SECOND_ID)
    assert venv_root.environment_key() != first
    identities["machine"] = bytes.fromhex(FIRST_ID)
    identities["user"] = b"1001"
    assert venv_root.environment_key() != first


def test_linux_machine_id_accepts_the_canonical_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "machine-id"
    monkeypatch.setattr(venv_root, "MACHINE_ID_FILE", identity)
    for content in (FIRST_ID + "\n", FIRST_ID):
        identity.write_bytes(content.encode("ascii"))
        assert venv_root.linux_machine_id() == bytes.fromhex(FIRST_ID)


@pytest.mark.parametrize(
    "value",
    (
        b"",
        b"uninitialized\n",
        b"0" * 32,
        b"0" * 32 + b"\n",
        b"A" * 32,
        b"g" * 32,
        b"1" * 31,
        b"1" * 33,
        b"1" * 32 + b"\n\n",
        b"1" * 32 + b"\x00",
        b"1" * 1000,
    ),
)
def test_invalid_linux_identity_has_no_shared_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: bytes
) -> None:
    identity = tmp_path / "machine-id"
    identity.write_bytes(value)
    monkeypatch.setattr(venv_root, "MACHINE_ID_FILE", identity)
    with pytest.raises(venv_root.MachineIdentityError, match="invalid or uninitialized"):
        venv_root.linux_machine_id()


def test_windows_machine_guid_is_validated_case_insensitively() -> None:
    guid = "01234567-89ab-cdef-0123-456789abcdef"
    assert venv_root.parse_windows_machine_guid(guid) == bytes.fromhex(guid.replace("-", ""))
    assert venv_root.parse_windows_machine_guid(guid.upper()) == bytes.fromhex(
        guid.replace("-", "")
    )
    for value in (
        None,
        "",
        "00000000-0000-0000-0000-000000000000",
        guid.replace("-", ""),
        "{" + guid + "}",
        guid + "\n",
        guid.replace("0", "g"),
    ):
        with pytest.raises(venv_root.MachineIdentityError, match="invalid or uninitialized"):
            venv_root.parse_windows_machine_guid(value)


def test_user_identity_uses_uid_or_casefolded_windows_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(venv_root.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(venv_root.getpass, "getuser", lambda: "Builder")
    assert venv_root.user_identity("linux") == b"1000"
    assert venv_root.user_identity("win32") == b"builder"


@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows registry")
def test_reads_the_windows_machine_guid() -> None:
    identity = venv_root.machine_identity()
    assert len(identity) == 16
    assert any(identity)


def test_selector_reports_failure_without_identity_or_path_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = tmp_path / "missing"
    monkeypatch.setattr(venv_root, "MACHINE_ID_FILE", identity)
    monkeypatch.setattr(venv_root, "machine_identity", venv_root.linux_machine_id)
    assert venv_root.main() == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "cannot read /etc/machine-id" in captured.err
    assert str(tmp_path) not in captured.err

    identity.write_bytes(FIRST_ID.encode("ascii"))
    assert venv_root.main() == 0
    captured = capsys.readouterr()
    assert captured.out == f"{venv_root.repository_venv_root()}\n"
    assert captured.out.startswith(".venvs/")
    assert FIRST_ID not in captured.out
    assert not captured.err


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    identity = tmp_path / "machine-id"
    identity.write_bytes((FIRST_ID + "\n").encode("ascii"))
    source = (ROOT / "scripts" / "venv_root.py").read_text(encoding="utf-8")
    copied = source.replace(
        'MACHINE_ID_FILE = Path("/etc/machine-id")',
        f"MACHINE_ID_FILE = Path({str(identity)!r})",
    )
    assert copied != source
    checkout = tmp_path / "repository"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "scripts" / "venv_root.py").write_text(copied, encoding="utf-8")
    shutil.copy2(ROOT / ".version", checkout / ".version")
    (checkout / "requirements-test.txt").touch()
    monkeypatch.setattr(venv_root, "MACHINE_ID_FILE", identity)
    monkeypatch.setattr(venv_root, "machine_identity", venv_root.linux_machine_id)
    return checkout


def _make(checkout: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-f",
            str(MAKEFILE),
            f"PY={sys.executable}",
            *arguments,
        ],
        cwd=checkout,
        text=True,
        capture_output=True,
        check=False,
    )


@make_integration
def test_make_selects_only_this_host_environments_and_fails_closed(repository: Path) -> None:
    report = "print-venvs:\n\t@printf '%s\\n' " + " ".join(
        f"'$({variable})'" for variable in ENVIRONMENTS
    )
    root = venv_root.repository_venv_root()

    selected = _make(repository, f"--eval={report}", "print-venvs")
    assert selected.returncode == 0, selected.stderr
    assert selected.stdout.splitlines() == [
        f"{root}/{_environment_name(variable)}" for variable in ENVIRONMENTS
    ]
    prepared = _make(repository, "-n", "venv-test")
    assert prepared.returncode == 0, prepared.stderr
    assert f"-m venv {root}/venv-test" in prepared.stdout
    cleanup = _make(repository, "-n", "clean")
    assert cleanup.returncode == 0, cleanup.stderr
    for variable in ENVIRONMENTS:
        assert f"{root}/{_environment_name(variable)}" in cleanup.stdout
    assert "rm -rf venv " not in cleanup.stdout

    venv_root.MACHINE_ID_FILE.write_bytes(b"uninitialized\n")
    refused = _make(repository, f"--eval={report}", "print-venvs")
    assert refused.returncode != 0
    assert not refused.stdout
    assert "Cannot select machine-specific environments" in refused.stderr
    assert "invalid or uninitialized" in refused.stderr


@make_integration
def test_make_clean_preserves_other_host_and_legacy_environments(repository: Path) -> None:
    first = repository / venv_root.repository_venv_root()
    for variable in ENVIRONMENTS:
        (first / _environment_name(variable)).mkdir(parents=True)
    venv_root.MACHINE_ID_FILE.write_bytes(SECOND_ID.encode("ascii"))
    second = repository / venv_root.repository_venv_root()
    assert second != first
    kept = []
    for parent in (second, repository):
        for variable in ENVIRONMENTS:
            cache = parent / _environment_name(variable) / "lib" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.pyc").write_bytes(b"keep")
            kept.append(cache / "module.pyc")
    source_cache = repository / "src" / "package" / "__pycache__"
    source_cache.mkdir(parents=True)
    venv_root.MACHINE_ID_FILE.write_bytes(FIRST_ID.encode("ascii"))

    completed = _make(repository, "clean")

    assert completed.returncode == 0, completed.stderr
    assert not first.exists()
    assert not source_cache.exists()
    assert all(path.read_bytes() == b"keep" for path in kept)
