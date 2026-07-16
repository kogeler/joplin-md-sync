#!/usr/bin/env python3
"""Bootstrap joplin-md-sync from a checkout using only the standard library.

Steps:
1. verify the Python version;
2. optionally create a virtual environment (--venv PATH);
3. install the current checkout with pip;
4. print the installed version;
5. run a CLI smoke test.

Nothing is downloaded from anywhere except the regular pip machinery for
this package itself.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import venv
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MINIMUM = (3, 13)


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", metavar="PATH", help="create/use a virtual environment at PATH")
    args = parser.parse_args()

    if sys.version_info < MINIMUM:
        print(
            f"error: Python {'.'.join(map(str, MINIMUM))}+ required, "
            f"found {sys.version.split()[0]}",
            file=sys.stderr,
        )
        return 1
    print(f"python {sys.version.split()[0]} ok")

    python = sys.executable
    if args.venv:
        venv_dir = Path(args.venv)
        if not venv_dir.exists():
            print(f"creating virtual environment at {venv_dir}")
            venv.EnvBuilder(with_pip=True).create(venv_dir)
        bin_dir = "Scripts" if sys.platform == "win32" else "bin"
        python = str(venv_dir / bin_dir / ("python.exe" if sys.platform == "win32" else "python"))

    run([python, "-m", "pip", "install", "--quiet", str(REPO)])
    run([python, "-m", "joplin_md_sync", "version"])
    # Smoke test: capabilities must succeed and be valid JSON.
    out = subprocess.run(
        [python, "-m", "joplin_md_sync", "capabilities", "--json"],
        check=True, capture_output=True, text=True,
    )
    import json

    payload = json.loads(out.stdout)
    assert payload["code"] == "OK", payload
    print("smoke test ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
