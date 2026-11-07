"""Build the native and zipapp OAuth packaging probes and run them outside checkout.

The native probe uses the release PyInstaller options with the probe as its
only entry, because the shipped one-file executable cannot run probe code.
``--native-only`` builds just that executable for ``make smoke-standalone``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

from build_standalone import oauth_pyinstaller_options

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / ".artifacts" / "oauth-probe"
NATIVE = (
    OUTPUT
    / "dist"
    / ("oauth-packaging-probe.exe" if sys.platform == "win32" else "oauth-packaging-probe")
)


def build_native() -> Path:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--onefile",
                "--console",
                "--name",
                "oauth-packaging-probe",
                "--distpath",
                str(OUTPUT / "dist"),
                "--workpath",
                str(OUTPUT / "build"),
                "--specpath",
                str(OUTPUT),
                "--paths",
                str(REPO / "src"),
                "--add-data",
                f"{REPO / '.version'}{os.pathsep}joplin_md_sync",
                *oauth_pyinstaller_options(),
                str(REPO / "tests" / "oauth" / "packaging_probe.py"),
            ],
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    if not NATIVE.is_file():
        raise RuntimeError(f"PyInstaller did not create {NATIVE}")
    return NATIVE


def build() -> None:
    native = build_native()
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
        archive = OUTPUT / "probe.pyz"
        zipapp.create_archive(source, archive)
        for command in ([str(native)], [sys.executable, str(archive)]):
            subprocess.run(command, cwd=temp, env=env, timeout=120, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--native-only", action="store_true", help="only build the native probe executable"
    )
    if parser.parse_args().native_only:
        print(build_native())
    else:
        build()
