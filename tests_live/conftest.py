"""Pytest lifecycle for the isolated live Joplin profile."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests_live.ephemeral_joplin import EphemeralJoplin, RunningJoplin, set_running_joplin


@pytest.fixture(scope="session", autouse=True)
def ephemeral_joplin() -> Iterator[RunningJoplin]:
    # Live tests use only the ephemeral profile; an exported developer token or
    # address would otherwise conflict with its token file or redirect calls.
    with pytest.MonkeyPatch.context() as environment:
        for name in ("JOPLIN_TOKEN", "JOPLIN_BASE_URL", "JOPLIN_PORT"):
            environment.delenv(name, raising=False)
        instance = EphemeralJoplin()
        try:
            runtime = instance.start()
            set_running_joplin(runtime)
            yield runtime
        finally:
            set_running_joplin(None)
            instance.stop()
