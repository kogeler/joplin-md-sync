"""Repository test gates for the standard runtime and required OAuth E2E."""

from __future__ import annotations

import functools
import os
from collections.abc import Iterable
from pathlib import Path

import pytest

from scripts.report_oauth_acceptance import (
    INVENTORY,
    Inventory,
    InventoryError,
    case_id_from_nodeid,
    collection_problems,
    load_inventory,
)


def pytest_addoption(parser):
    parser.addoption(
        "--require-oauth-e2e",
        action="store_true",
        help="require every inventoried OAuth E2E case exactly, without skips",
    )
    parser.addoption(
        "--oauth-e2e-inventory",
        type=Path,
        default=INVENTORY,
        help="exact OAuth E2E case inventory (default: tests/oauth/e2e/requirements.json)",
    )


@functools.cache
def _load(path: Path) -> Inventory:
    return load_inventory(path)


def required_inventory(config) -> Inventory:
    try:
        return _load(Path(config.getoption("--oauth-e2e-inventory")).resolve())
    except InventoryError as error:
        raise pytest.UsageError(str(error)) from None


def check_required_collection(config, nodeids: Iterable[str]) -> None:
    problems = collection_problems(required_inventory(config), nodeids)
    if problems:
        raise pytest.UsageError("\n".join(problems))


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--require-oauth-e2e"):
        return
    inventory = required_inventory(config)
    for item in items:
        case = case_id_from_nodeid(item.nodeid)
        if case in inventory.cases and not inventory.applicable(case, os.name):
            # The inventory owns applicability: another platform's case never
            # runs, so it cannot count here, whatever the test itself declares.
            item.add_marker(
                pytest.mark.skip(reason=f"OAuth E2E case is not applicable on {os.name}")
            )


def pytest_collection_finish(session):
    # xdist workers would only crash the scheduler here; the controller checks
    # every worker collection instead and reports the same usage error.
    if session.config.getoption("--require-oauth-e2e") and not hasattr(
        session.config, "workerinput"
    ):
        check_required_collection(session.config, (item.nodeid for item in session.items))


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    if node.config.getoption("--require-oauth-e2e"):
        check_required_collection(node.config, ids)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if not item.config.getoption("--require-oauth-e2e"):
        return report
    inventory = required_inventory(item.config)
    case = case_id_from_nodeid(item.nodeid)
    listed = case in inventory.cases
    if (
        (item.get_closest_marker("oauth_e2e") or listed)
        and (not listed or inventory.applicable(case, os.name))
        and (report.skipped or hasattr(report, "wasxfail"))
    ):
        report.outcome = "failed"
        report.longrepr = "A mandatory OAuth E2E case was skipped or expected to fail"
        if hasattr(report, "wasxfail"):
            del report.wasxfail
    return report
