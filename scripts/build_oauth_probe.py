"""Reproduce the E0 native and zipapp dependency/crypto smoke outside checkout."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

from build_standalone import oauth_pyinstaller_options

REPO = Path(__file__).resolve().parent.parent


def build() -> None:
    output = REPO / ".artifacts" / "oauth-probe"
    output.mkdir(parents=True, exist_ok=True)
    with (output / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--onefile",
                "--name",
                "oauth-packaging-probe",
                "--distpath",
                str(output / "dist"),
                "--workpath",
                str(output / "build"),
                "--specpath",
                str(output),
                "--paths",
                str(REPO / "src"),
                *oauth_pyinstaller_options(),
                str(REPO / "tests" / "oauth" / "packaging_probe.py"),
            ],
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    native = (
        output
        / "dist"
        / ("oauth-packaging-probe.exe" if sys.platform == "win32" else "oauth-packaging-probe")
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="jms-oauth-artifact-") as temp:
        source = Path(temp) / "source"
        shutil.copytree(
            REPO / "src" / "joplin_md_sync",
            source / "joplin_md_sync",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copyfile(REPO / "tests" / "oauth" / "packaging_probe.py", source / "__main__.py")
        archive = output / "probe.pyz"
        zipapp.create_archive(source, archive)
        for command in ([str(native)], [sys.executable, str(archive)]):
            subprocess.run(command, cwd=temp, env=env, timeout=60, check=True)


if __name__ == "__main__":
    build()
