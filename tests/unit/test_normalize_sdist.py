"""Tests for reproducible source distribution normalization."""

from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

import pytest

from scripts.normalize_sdist import normalize


def _archive(path: Path, *, timestamp: int, owner: int) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=timestamp) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as target:
                directory = tarfile.TarInfo("package/")
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                directory.mtime = timestamp
                directory.uid = owner
                directory.gid = owner
                directory.uname = f"user-{owner}"
                directory.gname = f"group-{owner}"
                directory.pax_headers = {"mtime": f"{timestamp}.25"}
                target.addfile(directory)

                data = b"same package contents\n"
                member = tarfile.TarInfo("package/module.py")
                member.mode = 0o644
                member.size = len(data)
                member.mtime = timestamp
                member.uid = owner
                member.gid = owner
                member.uname = f"user-{owner}"
                member.gname = f"group-{owner}"
                target.addfile(member, io.BytesIO(data))


def test_normalization_makes_equivalent_sdist_archives_byte_identical(tmp_path: Path) -> None:
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    _archive(first, timestamp=1_700_000_001, owner=1000)
    _archive(second, timestamp=1_800_000_002, owner=2000)

    normalize(first, epoch=1_600_000_000)
    normalize(second, epoch=1_600_000_000)

    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first, mode="r:gz") as archive:
        for member in archive.getmembers():
            assert member.mtime == 1_600_000_000
            assert member.uid == 0
            assert member.gid == 0
            assert member.uname == ""
            assert member.gname == ""
            assert "mtime" not in member.pax_headers


def test_normalization_rejects_a_negative_epoch(tmp_path: Path) -> None:
    archive = tmp_path / "package.tar.gz"
    _archive(archive, timestamp=1_700_000_001, owner=1000)
    with pytest.raises(ValueError, match="non-negative"):
        normalize(archive, epoch=-1)
