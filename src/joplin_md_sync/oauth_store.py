"""Private, encrypted and bounded local OAuth state, with exclusive ownership."""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import stat
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, SupportsFloat

from key_value.aio._utils.sanitization import AlwaysHashStrategy
from key_value.aio.stores.filetree import FileTreeStore, FileTreeV1CollectionSanitizationStrategy
from key_value.aio.wrappers.base import BaseWrapper
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from joplin_md_sync.auth import (
    read_protected_file_bytes,
    validate_private_directory,
    validate_protected_file,
)
from joplin_md_sync.errors import WorkspaceLockedError
from joplin_md_sync.locking import _lock_exclusive, _unlock
from joplin_md_sync.oauth_config import OAuthConfig

STATE_SCHEMA = {"schema": 1, "provider": "github", "engine": "filetree-encrypted-v1"}
MAX_STATE_FILES = 4096
MAX_STATE_BYTES = 256 * 1024 * 1024
MAX_VALUE_BYTES = 65536
MAX_STATE_TTL = 15897600


def inspect_state(directory: Path) -> bool:
    """Validate existing state without creating, migrating or locking it."""
    for path in [directory, *directory.parents]:
        if path.is_symlink() or path.is_junction():
            raise ValueError("OAuth state path must not contain symlinks or junctions")
    if not directory.exists():
        return False
    validate_private_directory(directory, label="OAuth state directory")
    if any(path.name not in {".lock", "schema.json", "store"} for path in directory.iterdir()):
        raise ValueError("OAuth state requires a dedicated directory")
    schema = directory / "schema.json"
    if not schema.exists():
        if (directory / "store").exists():
            raise ValueError("OAuth state schema is missing; existing state was preserved")
        return False
    try:
        value = json.loads(read_protected_file_bytes(schema, label="OAuth schema", max_bytes=1024))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("OAuth state schema is corrupt") from None
    if value != STATE_SCHEMA:
        raise ValueError("OAuth state schema is incompatible")
    check_tree(directory)
    return True


def check_tree(directory: Path) -> tuple[int, int]:
    files = total = 0
    for path in directory.rglob("*"):
        info = path.lstat()
        if path.is_symlink() or path.is_junction():
            raise ValueError("OAuth state must not contain links or junctions")
        if stat.S_ISDIR(info.st_mode):
            validate_private_directory(path, label="OAuth state subdirectory")
        elif stat.S_ISREG(info.st_mode):
            if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
                raise ValueError("OAuth state files must be owner-only")
            if os.name == "nt":
                validate_protected_file(
                    path, label="OAuth state record", max_bytes=MAX_VALUE_BYTES * 2
                )
            files += 1
            total += info.st_size
        else:
            raise ValueError("OAuth state must contain only regular files and directories")
        if files > MAX_STATE_FILES or total > MAX_STATE_BYTES:
            raise ValueError("OAuth state quota exceeded")
    return files, total


class BoundedOAuthStore(BaseWrapper):
    def __init__(self, root: Path, key: bytes) -> None:
        self.root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        validate_private_directory(root, label="OAuth store directory")
        self.file_store = FileTreeStore(
            data_directory=root,
            key_sanitization_strategy=AlwaysHashStrategy(),
            collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(root),
        )
        self.key_value = FernetEncryptionWrapper(
            self.file_store,
            source_material=key.hex(),
            salt="joplin-md-sync-oauth-state-v1",
            raise_on_decryption_error=True,
        )
        self._write_lock = asyncio.Lock()
        self._last_cull = 0.0

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        lifetime = float(ttl) if ttl is not None else float(MAX_STATE_TTL)
        if not math.isfinite(lifetime) or lifetime <= 0:
            raise ValueError("OAuth state lifetime must be positive and finite")
        lifetime = min(lifetime, MAX_STATE_TTL)
        if len(json.dumps(dict(value), ensure_ascii=False).encode()) > MAX_VALUE_BYTES:
            raise ValueError("OAuth state record exceeds its size limit")
        async with self._write_lock:
            if time.monotonic() - self._last_cull > 300:
                await self.file_store.cull()
                self._last_cull = time.monotonic()
            files, total = check_tree(self.root)
            existing = await self.key_value.get(key, collection=collection)
            if existing is None and (
                files >= MAX_STATE_FILES - 16 or total >= MAX_STATE_BYTES - MAX_VALUE_BYTES * 2
            ):
                raise ValueError("OAuth state quota exceeded")
            await self.key_value.put(key, value, collection=collection, ttl=lifetime)

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        if len(keys) != len(values):
            raise ValueError("OAuth bulk keys and values must have equal lengths")
        for key, value in zip(keys, values, strict=True):
            await self.put(key, value, collection=collection, ttl=ttl)


class OAuthState:
    def __init__(self, config: OAuthConfig) -> None:
        self.config = config
        self.directory = config.state_dir
        self._fd: int | None = None
        self._umask: int | None = None
        self.store: BoundedOAuthStore | None = None

    def __enter__(self) -> OAuthState:
        try:
            if os.name == "posix":
                self._umask = os.umask(0o077)
            for path in [self.directory, *self.directory.parents]:
                if path.is_symlink() or path.is_junction():
                    raise ValueError("OAuth state path must not contain symlinks or junctions")
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            validate_private_directory(self.directory, label="OAuth state directory")
            allowed = {".lock", "schema.json", "store"}
            if any(path.name not in allowed for path in self.directory.iterdir()):
                raise ValueError("OAuth state requires a dedicated directory")
            lock_path = self.directory / ".lock"
            flags = (
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            )
            self._fd = os.open(lock_path, flags, 0o600)
            validate_protected_file(lock_path, label="OAuth state lock", max_bytes=64)
            info, opened = lock_path.lstat(), os.fstat(self._fd)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("OAuth state lock changed while opening")
            if os.name == "nt" and opened.st_size == 0:
                os.write(self._fd, b"\0")
                os.lseek(self._fd, 0, os.SEEK_SET)
            if not _lock_exclusive(self._fd):
                raise WorkspaceLockedError("OAuth state is in use by another process")
            schema_path = self.directory / "schema.json"
            if schema_path.exists():
                try:
                    current = json.loads(
                        read_protected_file_bytes(schema_path, label="OAuth schema", max_bytes=1024)
                    )
                except (ValueError, UnicodeDecodeError):
                    raise ValueError("OAuth state schema is corrupt") from None
                if current != STATE_SCHEMA:
                    raise ValueError("OAuth state schema is incompatible")
            else:
                if (self.directory / "store").exists():
                    raise ValueError("OAuth state schema is missing; existing state was preserved")
                with schema_path.open("x", encoding="utf-8") as output:
                    json.dump(STATE_SCHEMA, output)
                    output.flush()
                    os.fsync(output.fileno())
            check_tree(self.directory)
            self.store = BoundedOAuthStore(self.directory / "store", self.config.signing_key)
            return self
        except BaseException:
            self.close()
            raise

    def reset(self) -> None:
        if self._fd is None:
            raise ValueError("OAuth reset requires the state lock")
        check_tree(self.directory)
        store_path = self.directory / "store"
        if store_path.exists():
            shutil.rmtree(store_path)
        self.store = None

    def close(self) -> None:
        if self._fd is not None:
            _unlock(self._fd)
            os.close(self._fd)
            self._fd = None
        if self._umask is not None:
            os.umask(self._umask)
            self._umask = None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
