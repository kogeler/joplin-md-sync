"""Pytest lifecycle for the isolated live Joplin profile."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests_live.ephemeral_joplin import EphemeralJoplin, RunningJoplin, set_running_joplin


@pytest.fixture(scope="session", autouse=True)
def ephemeral_joplin() -> Iterator[RunningJoplin]:
    instance = EphemeralJoplin()
    try:
        runtime = instance.start()
        set_running_joplin(runtime)
        yield runtime
    finally:
        set_running_joplin(None)
        instance.stop()
