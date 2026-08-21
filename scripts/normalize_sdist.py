#!/usr/bin/env python3
"""Normalize generated sdist ownership and timestamps for reproducible hashes."""

from __future__ import annotations

import argparse
import copy
import gzip
import io
import os
import tarfile
import tempfile
from pathlib import Path


def normalize(path: Path, *, epoch: int) -> None:
    if epoch < 0:
        raise ValueError("epoch must be non-negative")
    with tarfile.open(path, mode="r:gz") as source:
        members = source.getmembers()
        contents = {
            member.name: source.extractfile(member).read()
            for member in members
            if member.isfile()
        }

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as raw:
            temporary = Path(raw.name)
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as target:
                    for original in members:
                        member = copy.copy(original)
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        member.mtime = epoch
                        member.pax_headers = {
                            key: value
                            for key, value in member.pax_headers.items()
                            if key not in {"atime", "ctime", "mtime"}
                        }
                        data = io.BytesIO(contents[member.name]) if member.isfile() else None
                        target.addfile(member, data)
        os.chmod(temporary, 0o644)
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        normalize(args.archive, epoch=args.epoch)
    except (OSError, ValueError, tarfile.TarError) as err:
        parser.exit(1, f"sdist normalization failed: {err}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
