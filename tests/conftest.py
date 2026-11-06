"""Repository test gates for the standard runtime and required OAuth E2E."""

from __future__ import annotations

import functools
import json
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

import pytest

MATRIX = Path(__file__).parent / "oauth/e2e/requirements.json"


def pytest_addoption(parser):
    parser.addoption(
        "--require-oauth-e2e",
        action="store_true",
        help="require all OAuth E2E scenarios without skips",
    )


@functools.cache
def required_oauth_nodes() -> frozenset[str]:
    matrix = json.loads(MATRIX.read_text())
    return frozenset(
        node for requirement in matrix["requirements"].values() for node in requirement["tests"]
    )


def oauth_e2e_node(nodeid: str) -> str | None:
    """Return the matrix form ``file.py::function`` of a collected E2E node ID."""
    path, _, name = nodeid.partition("::")
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if parts[-3:-1] != ("oauth", "e2e") or not name:
        return None
    return parts[-1] + "::" + name.rsplit("::", 1)[-1].split("[", 1)[0]


def check_required_collection(nodeids: Iterable[str]) -> None:
    collected = {oauth_e2e_node(nodeid) for nodeid in nodeids}
    missing = required_oauth_nodes() - collected
    if missing:
        raise pytest.UsageError(
            "Required OAuth E2E cases were not collected: " + ", ".join(sorted(missing))
        )


def pytest_collection_finish(session):
    # xdist workers would only crash the scheduler here; the controller checks
    # every worker collection instead and reports the same usage error.
    if session.config.getoption("--require-oauth-e2e") and not hasattr(
        session.config, "workerinput"
    ):
        check_required_collection(item.nodeid for item in session.items)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    if node.config.getoption("--require-oauth-e2e"):
        check_required_collection(ids)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if (
        item.config.getoption("--require-oauth-e2e")
        and (
            item.get_closest_marker("oauth_e2e")
            or oauth_e2e_node(item.nodeid) in required_oauth_nodes()
        )
        and (report.skipped or hasattr(report, "wasxfail"))
    ):
        report.outcome = "failed"
        report.longrepr = "A mandatory OAuth E2E scenario was skipped or expected to fail"
        if hasattr(report, "wasxfail"):
            del report.wasxfail
    return report
