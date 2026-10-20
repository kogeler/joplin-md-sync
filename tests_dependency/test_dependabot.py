"""Offline acceptance: real Dependabot code, pip-compile, wheels, and installers."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIENCES = ("dev", "test", "package", "docs")


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=180
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _wheel(
    directory: Path,
    name: str,
    version: str,
    child: str | None = None,
    constraint: str = ">=1,<3",
) -> None:
    """Publish an installable wheel without a build backend or external registry."""
    info = f"{name}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    if child:
        metadata += f"Requires-Dist: {child}{constraint}\n"
    code = f'VERSION = "{version}"\n'
    if child:
        code += f"import {child}\ndef probe():\n    return VERSION + ':' + {child}.probe()\n"
    else:
        code += "def probe():\n    return VERSION\n"
    contents = {
        f"{name}/__init__.py": code.encode(),
        f"{info}/METADATA": metadata.encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: acceptance\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = io.StringIO()
    writer = csv.writer(record)
    for path, data in contents.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((path, f"sha256={digest}", len(data)))
    writer.writerow((f"{info}/RECORD", "", ""))
    contents[f"{info}/RECORD"] = record.getvalue().encode()
    with zipfile.ZipFile(directory / f"{name}-{version}-py3-none-any.whl", "w") as archive:
        for path, data in contents.items():
            archive.writestr(path, data)


@pytest.fixture
def graph(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    resolver = os.environ.get("LOCK_PYTHON")
    assert resolver, "Run through make test-dependency-updates"
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _wheel(wheels, "fixture_leaf", "1.0")
    _wheel(wheels, "fixture_core", "1.0", "fixture_leaf")
    for audience in AUDIENCES:
        parent = f"fixture_{audience}"
        _wheel(wheels, parent, "1.0", "fixture_core")
        (tmp_path / f"requirements-{audience}.in").write_text(f"{parent}==1.0\n", encoding="utf-8")
    shutil.copy2(ROOT / "Makefile", tmp_path)
    (tmp_path / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / "venv_root.py", tmp_path / "scripts")
    # The acceptance environment uses the already bootstrapped exact resolver.
    # Only the synthetic graph is resolved against this isolated wheelhouse.
    (tmp_path / "resolver-ready").touch()
    # Keep host/registry credentials out of test subprocesses and pytest's
    # fixture diagnostics. The wheelhouse is the only package source.
    inherited = (
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "XDG_RUNTIME_DIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
    )
    env = {key: os.environ[key] for key in inherited if key in os.environ}
    env.update(
        LOCK_PYTHON=resolver,
        PIP_NO_INDEX="1",
        PIP_FIND_LINKS=str(wheels),
        PIP_CONFIG_FILE=os.devnull,
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        MAKEFLAGS="",
    )
    _run(_make(resolver, "lock"), cwd=tmp_path, env=env)
    for audience in AUDIENCES:
        lock = (tmp_path / f"requirements-{audience}.txt").read_text(encoding="utf-8")
        assert "fixture-core==1.0" in lock
        assert "fixture-leaf==1.0" in lock
    _wheel(wheels, "fixture_leaf", "2.0")
    _wheel(wheels, "fixture_core", "2.0", "fixture_leaf")
    return tmp_path, env


def _make(resolver: str, target: str) -> list[str]:
    return [
        "make",
        target,
        f"PY={sys.executable}",
        f"PYTHON_LOCK={resolver}",
        "DEPS_LOCK_STAMP=resolver-ready",
    ]


def _updater(root: Path, env: dict[str, str], scenario: str) -> dict[str, object]:
    container = os.environ.get("CONTAINER")
    image = os.environ.get("DEPENDABOT_IMAGE")
    assert container and image, "Run through make test-dependency-updates"
    assert "@sha256:" in image
    output = _run(
        [
            *shlex.split(container),
            "run",
            "--rm",
            "--pull=never",
            "--user=0",
            "--network=none",
            "--cap-drop=all",
            "--cap-add=DAC_OVERRIDE",
            "--security-opt=no-new-privileges",
            "--volume",
            f"{ROOT / '.github' / 'dependabot.yml'}:/acceptance/dependabot.yml:ro,z",
            "--volume",
            f"{ROOT / 'tests_dependency' / 'updater.rb'}:/acceptance/updater.rb:ro,z",
            "--volume",
            f"{root}:{root}:rw,z",
            "--env",
            "PIP_NO_INDEX=1",
            "--env",
            f"PIP_FIND_LINKS={root / 'wheels'}",
            "--env",
            "PIP_CONFIG_FILE=/dev/null",
            image,
            "bundle",
            "exec",
            "ruby",
            "-I/home/dependabot/dependabot-updater/lib",
            "/acceptance/updater.rb",
            str(root),
            scenario,
        ],
        cwd=ROOT,
        env=env,
    )
    return json.loads(output.split("ACCEPTANCE_RESULT=", 1)[1])


@pytest.mark.parametrize("scenario", ("update", "security"))
def test_dependabot_updates_the_whole_shared_graph_and_is_idempotent(
    graph: tuple[Path, dict[str, str]],
    scenario: str,
) -> None:
    root, env = graph
    inputs = {path.name: path.read_bytes() for path in root.glob("*.in")}
    result = _updater(root, env, scenario)
    assert result["updated_locks"] == [f"requirements-{item}.txt" for item in AUDIENCES]
    assert result["second_update"] == []
    assert inputs == {path.name: path.read_bytes() for path in root.glob("*.in")}
    resolver = env["LOCK_PYTHON"]
    before = {path.name: path.read_bytes() for path in root.glob("*.txt")}
    _run(_make(resolver, "freeze-check"), cwd=root, env=env)
    _run(_make(resolver, "lock"), cwd=root, env=env)
    assert before == {path.name: path.read_bytes() for path in root.glob("*.txt")}
    for audience in AUDIENCES:
        venv = root / f"installed-{audience}"
        _run([sys.executable, "-m", "venv", "--without-pip", str(venv)], cwd=root, env=env)
        python = venv / "bin" / "python"
        _run(
            [
                resolver,
                "-m",
                "pip",
                "--python",
                str(python),
                "install",
                "--require-hashes",
                "--only-binary=:all:",
                "--requirement",
                f"requirements-{audience}.txt",
            ],
            cwd=root,
            env=env,
        )
        probe = _run(
            [
                python.as_posix(),
                "-c",
                f"import fixture_{audience}; print(fixture_{audience}.probe())",
            ],
            cwd=root,
            env=env,
        )
        assert probe.strip() == "1.0:2.0:2.0"


def test_dependabot_updates_direct_input_and_lock_together(
    graph: tuple[Path, dict[str, str]],
) -> None:
    root, env = graph
    _wheel(root / "wheels", "fixture_dev", "2.0", "fixture_core", ">=2,<3")
    before = {path.name: path.read_bytes() for path in root.glob("requirements-*.*")}
    result = _updater(root, env, "direct")
    assert result["updated_files"] == ["requirements-dev.in", "requirements-dev.txt"]
    assert (root / "requirements-dev.in").read_text(encoding="utf-8") == "fixture_dev==2.0\n"
    for name, content in before.items():
        if name not in result["updated_files"]:
            assert (root / name).read_bytes() == content
    _run(_make(env["LOCK_PYTHON"], "freeze-check"), cwd=root, env=env)


def test_dependabot_rejects_incompatible_update_without_modifying_files(
    graph: tuple[Path, dict[str, str]],
) -> None:
    root, env = graph
    # One audience still has a parent that cannot consume the proposed update.
    _wheel(root / "wheels", "fixture_blocker", "1.0", "fixture_core", "==1.0")
    path = root / "requirements-docs.in"
    path.write_text("fixture_docs==1.0\nfixture_blocker==1.0\n", encoding="utf-8")
    _run(_make(env["LOCK_PYTHON"], "lock"), cwd=root, env=env)
    before = {path.name: path.read_bytes() for path in root.glob("requirements-*.*")}
    assert _updater(root, env, "incompatible")["rejected"] is True
    assert before == {path.name: path.read_bytes() for path in root.glob("requirements-*.*")}
