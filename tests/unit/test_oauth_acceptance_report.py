"""The exact OAuth E2E inventory, its collection gate and the evidence report.

Synthetic suites run in real pytest subprocesses, serially and under xdist, so
the repository gate hooks and real JUnit output are exercised end to end.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.report_oauth_acceptance import (
    INVENTORY,
    InventoryError,
    case_id_from_junit,
    case_id_from_nodeid,
    load_inventory,
    parse_inventory,
    refresh_inventory,
    report,
    write_inventory,
)

ROOT = Path(__file__).resolve().parents[2]
HERE = os.name
OTHER = {"posix": "nt", "nt": "posix"}[HERE]
CASE_FOR = {"posix": "test_sample.py::test_posix_only", "nt": "test_sample.py::test_windows_only"}
VARIANT_A = "test_sample.py::test_variant[a]"
VARIANT_B = "test_sample.py::test_variant[b]"
SINGLE = "test_sample.py::test_single"
SAMPLE = """\
import os

import pytest

MODE = os.environ.get("SAMPLE_MODE", "")


@pytest.mark.parametrize("value", ["a", "b"])
def test_variant(value):
    if MODE == "skip-" + value:
        pytest.skip("synthetic skip")
    if MODE == "xfail-" + value:
        pytest.xfail("synthetic expected failure")


def test_single():
    assert MODE != "fail-single", "synthetic failure"


@pytest.mark.skipif(os.name != "posix", reason="POSIX-specific synthetic case")
def test_posix_only():
    pass


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific synthetic case")
def test_windows_only():
    pass
"""


def sample_inventory() -> dict:
    return {
        "requirements": {
            "R1": {"description": "Every variant", "cases": [VARIANT_A, VARIANT_B]},
            "R2": {
                "description": "Single and platform cases",
                "cases": [SINGLE, CASE_FOR["posix"], CASE_FOR["nt"]],
            },
        },
        "platforms": {CASE_FOR["posix"]: ["posix"], CASE_FOR["nt"]: ["nt"]},
        "external": {"X1": {"description": "Separate gate", "command": "make smoke"}},
    }


def write_suite(root: Path) -> Path:
    (root / "oauth" / "e2e").mkdir(parents=True)
    (root / "oauth" / "e2e" / "test_sample.py").write_text(SAMPLE, encoding="utf-8")
    # Anchor the synthetic rootdir so node IDs keep the oauth/e2e directory.
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return root


def run_suite(
    root: Path,
    inventory: dict | str,
    *,
    workers: str | None = None,
    mode: str = "",
    gate: bool = True,
) -> subprocess.CompletedProcess[str]:
    path = root / "inventory.json"
    path.write_text(
        inventory if isinstance(inventory, str) else json.dumps(inventory), encoding="utf-8"
    )
    env = {name: value for name, value in os.environ.items() if not name.startswith("PYTEST_")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH", "")]))
    env["SAMPLE_MODE"] = mode
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "tests.conftest",
        "-p",
        "no:cacheprovider",
        f"--oauth-e2e-inventory={path}",
        f"--junitxml={root / 'junit.xml'}",
        "oauth/e2e",
    ]
    if gate:
        command.append("--require-oauth-e2e")
    if workers:
        command.extend(["-n", workers])
    result = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    # The xdist controller, not a worker, must apply the collection gate.
    assert not workers or f"created: {workers}/{workers} workers" in result.stdout, result.stdout
    return result


def evidence(root: Path, junit: Path | None = None, *, platform: str = HERE) -> dict:
    """Run the report; return its JSON with the failure message, if any."""
    output = root / "evidence.json"
    message = None
    try:
        report(
            junit or root / "junit.xml",
            output,
            inventory_path=root / "inventory.json",
            platform=platform,
        )
    except SystemExit as error:
        message = str(error)
    data = json.loads(output.read_text(encoding="utf-8"))
    data["failure"] = message
    return data


@pytest.fixture(params=["serial", "xdist"])
def workers(request) -> str | None:
    return "2" if request.param == "xdist" else None


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    return write_suite(tmp_path)


@pytest.fixture(scope="module")
def valid_run(tmp_path_factory) -> Path:
    root = write_suite(tmp_path_factory.mktemp("valid"))
    result = run_suite(root, sample_inventory())
    assert result.returncode == 0, result.stdout + result.stderr
    return root


def edited_junit(root: Path, tmp_path: Path, edit) -> Path:
    """Copy the valid run's JUnit, edit its single suite and keep counts consistent."""
    (tmp_path / "inventory.json").write_text(json.dumps(sample_inventory()), encoding="utf-8")
    tree = ET.parse(root / "junit.xml")
    suite = tree.getroot().find("testsuite")
    cases = suite.findall("testcase")
    edit(suite, {case_id_from_junit(c.get("classname"), c.get("name")): c for c in cases})
    suite.set("tests", str(len(suite.findall("testcase"))))
    path = tmp_path / "edited.xml"
    tree.write(path, encoding="utf-8")
    return path


def test_valid_run_passes_gate_and_report(suite, workers):
    result = run_suite(suite, sample_inventory(), workers=workers)
    assert result.returncode == 0, result.stdout + result.stderr
    data = evidence(suite)
    assert data["failure"] is None
    assert data["platform"] == HERE
    assert data["functional_requirements"]["R1"]["status"] == "passed"
    assert data["functional_requirements"]["R2"]["cases"] == {
        SINGLE: "passed",
        CASE_FOR[HERE]: "passed",
        CASE_FOR[OTHER]: "not_applicable",
    }
    assert data["not_applicable_cases"] == [CASE_FOR[OTHER]]
    assert data["missing_cases"] == data["unlisted_cases"] == data["duplicate_cases"] == []
    assert (data["passed_cases"], data["failed_cases"], data["skipped_cases"]) == (4, 0, 0)
    assert data["external_gates"] == sample_inventory()["external"]
    assert "tests/oauth/e2e/requirements.json" in data["source_sha256"]


def _add_case(case):
    def change(data):
        data["requirements"]["R1"]["cases"].append(case)

    return change


def _drop_variant(data):
    data["requirements"]["R1"]["cases"].remove(VARIANT_B)


def _duplicate_record(data):
    data["requirements"]["R1"]["cases"].append(VARIANT_A)


def _unknown_platform(data):
    data["platforms"][SINGLE] = ["linux"]


def _unmapped(data):
    data["unmapped"] = ["test_sample.py::test_new"]


# Two objects under one key: plain JSON loading would silently keep the last.
CONFLICTING = (
    '{"requirements": {"R1": '
    + json.dumps({"description": "A", "cases": [VARIANT_A]})
    + ', "R1": '
    + json.dumps({"description": "B", "cases": [VARIANT_B]})
    + "}}"
)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(
            _add_case("test_sample.py::test_variant[c]"),
            "were not collected: test_sample.py::test_variant[c]",
            id="missing-variant",
        ),
        pytest.param(
            _add_case("test_sample.py::test_removed"),
            "were not collected: test_sample.py::test_removed",
            id="missing-function",
        ),
        pytest.param(_drop_variant, "not in the exact inventory", id="unlisted-variant"),
        pytest.param(_duplicate_record, "R1 lists duplicate cases", id="duplicate-record"),
        pytest.param(CONFLICTING, "Conflicting duplicate inventory keys: R1", id="conflict"),
        pytest.param(_unknown_platform, "platforms for test_sample.py", id="bad-platform"),
        pytest.param(_unmapped, "cases without requirement IDs", id="unmapped"),
    ],
)
def test_collection_gate_rejects_an_inexact_inventory(suite, workers, change, message):
    if isinstance(change, str):
        inventory = change
    else:
        inventory = sample_inventory()
        change(inventory)
    result = run_suite(suite, inventory, workers=workers)
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert message in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("mode", "case"),
    [("skip-a", VARIANT_A), ("xfail-b", VARIANT_B), ("fail-single", SINGLE)],
)
def test_runtime_gate_fails_skipped_expected_failure_and_failed_cases(suite, workers, mode, case):
    result = run_suite(suite, sample_inventory(), workers=workers, mode=mode)
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout + result.stderr
    data = evidence(suite)
    assert data["rejected_cases"] == {case: "failed"}
    assert data["failure"] is not None


def test_applicable_platform_case_cannot_be_skipped(suite, workers):
    inventory = sample_inventory()
    # Claim the other platform's case applies here too: its own skip now fails.
    inventory["platforms"][CASE_FOR[OTHER]] = [HERE, OTHER]
    result = run_suite(suite, inventory, workers=workers)
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout + result.stderr
    # A marker skip happens during setup, so JUnit records the conversion as an error.
    assert evidence(suite)["rejected_cases"] == {CASE_FOR[OTHER]: "error"}


def test_inventory_skips_a_case_for_another_platform_even_without_its_marker(suite):
    inventory = sample_inventory()
    # This platform's case would run and pass; declaring it for the other
    # platform makes the gate skip it, so it can never count here.
    inventory["platforms"] = {case: [OTHER] for case in CASE_FOR.values()}
    inventory["requirements"]["R2"]["cases"] = [SINGLE, CASE_FOR[HERE]]
    inventory["requirements"]["R3"] = {"description": "Other", "cases": [CASE_FOR[OTHER]]}
    result = run_suite(suite, inventory)
    assert result.returncode == pytest.ExitCode.OK, result.stdout + result.stderr
    data = evidence(suite)
    assert data["failure"] is None
    assert sorted(data["not_applicable_cases"]) == sorted(CASE_FOR.values())
    assert data["functional_requirements"]["R3"]["status"] == "not_applicable"


@pytest.mark.parametrize(
    ("mode", "case", "status"),
    [("skip-a", VARIANT_A, "skipped"), ("xfail-b", VARIANT_B, "xfail")],
)
def test_report_rejects_skips_recorded_without_the_gate(suite, mode, case, status):
    result = run_suite(suite, sample_inventory(), mode=mode, gate=False)
    assert result.returncode == pytest.ExitCode.OK, result.stdout + result.stderr
    data = evidence(suite)
    assert data["rejected_cases"] == {case: status}
    assert data["functional_requirements"]["R1"]["status"] == "incomplete"
    assert data["skipped_cases"] == 1
    assert "required cases did not all pass" in data["failure"]


@pytest.mark.parametrize("case", [VARIANT_B, SINGLE], ids=["variant", "function"])
def test_report_lists_missing_variant_or_function(valid_run, tmp_path, case):
    junit = edited_junit(valid_run, tmp_path, lambda suite, cases: suite.remove(cases[case]))
    data = evidence(tmp_path, junit)
    assert data["missing_cases"] == [case]
    requirement = "R1" if case == VARIANT_B else "R2"
    assert data["functional_requirements"][requirement]["missing"] == [case]
    assert data["functional_requirements"][requirement]["status"] == "incomplete"
    assert data["failure"] is not None


def test_report_rejects_duplicate_records(valid_run, tmp_path):
    junit = edited_junit(
        valid_run, tmp_path, lambda suite, cases: suite.append(copy.deepcopy(cases[SINGLE]))
    )
    data = evidence(tmp_path, junit)
    assert data["duplicate_cases"] == [SINGLE]
    assert data["rejected_cases"] == {SINGLE: "duplicate"}
    assert "duplicate or conflicting" in data["failure"]


def test_report_rejects_xpass_and_e2e_collection_failures(valid_run, tmp_path):
    def edit(suite, cases):
        # pytest records a strict xpass as a skip with this fixed message.
        ET.SubElement(cases[SINGLE], "skipped", message="xfail-marked test passes unexpectedly")
        broken = ET.SubElement(suite, "testcase", classname="", name="oauth.e2e.test_broken")
        ET.SubElement(broken, "error", message="collection failure")
        suite.set("skipped", str(int(suite.get("skipped")) + 1))
        suite.set("errors", str(int(suite.get("errors")) + 1))

    data = evidence(tmp_path, edited_junit(valid_run, tmp_path, edit))
    assert data["rejected_cases"] == {SINGLE: "xpass"}
    assert data["evidence_errors"][0] == "OAuth E2E collection failed: oauth.e2e.test_broken"


def test_report_rejects_unlisted_records(valid_run, tmp_path):
    def unlisted(suite, cases):
        extra = copy.deepcopy(cases[SINGLE])
        extra.set("name", "test_unlisted")
        suite.append(extra)

    data = evidence(tmp_path, edited_junit(valid_run, tmp_path, unlisted))
    assert data["unlisted_cases"] == ["test_sample.py::test_unlisted"]
    assert "not in the exact inventory" in data["failure"]


def test_case_for_another_platform_never_counts(valid_run, tmp_path):
    (tmp_path / "inventory.json").write_text(json.dumps(sample_inventory()), encoding="utf-8")
    data = evidence(tmp_path, valid_run / "junit.xml", platform=OTHER)
    assert data["rejected_cases"] == {
        CASE_FOR[HERE]: "ran_on_non_applicable_platform",
        CASE_FOR[OTHER]: "skipped",
    }
    assert data["functional_requirements"]["R2"]["status"] == "incomplete"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "JUnit results not found"),
        ("<testsuites><testsuite", "Malformed JUnit XML"),
        ("<report/>", "unexpected root <report>"),
        ("<testsuites/>", "no test suite"),
        (
            '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase/></testsuite>',
            "testcase without identity",
        ),
    ],
    ids=["missing", "truncated", "root", "empty", "identity"],
)
def test_report_rejects_missing_or_malformed_junit(tmp_path, content, message):
    (tmp_path / "inventory.json").write_text(json.dumps(sample_inventory()), encoding="utf-8")
    junit = tmp_path / "junit.xml"
    if content is not None:
        junit.write_text(content, encoding="utf-8")
    data = evidence(tmp_path)
    assert data["failure"].startswith("OAuth acceptance evidence is invalid")
    assert message in data["evidence_errors"][0]
    assert "functional_requirements" not in data


def test_report_rejects_incomplete_suite_counts(valid_run, tmp_path):
    junit = edited_junit(valid_run, tmp_path, lambda suite, cases: suite.set("failures", "1"))
    data = evidence(tmp_path, junit)
    assert "Incomplete JUnit XML" in data["evidence_errors"][0]


def test_report_accepts_subtest_reports_counted_without_testcases(valid_run, tmp_path):
    # A full-suite run with unittest subTest reports declares more tests than
    # it has <testcase> elements; this must not invalidate the evidence.
    junit = edited_junit(
        valid_run,
        tmp_path,
        lambda suite, cases: suite.set("tests", str(int(suite.get("tests")) + 17)),
    )
    data = evidence(tmp_path, junit)
    assert not data["evidence_errors"]


def test_report_rejects_an_invalid_inventory(valid_run, tmp_path):
    (tmp_path / "inventory.json").write_text('{"requirements": {}}', encoding="utf-8")
    data = evidence(tmp_path, valid_run / "junit.xml")
    assert "'requirements' must be a non-empty object" in data["evidence_errors"][0]


@pytest.mark.parametrize(
    ("nodeid", "case", "classname", "name"),
    [
        (
            "tests/oauth/e2e/test_a.py::test_b[/mcp//alias]",
            "test_a.py::test_b[/mcp//alias]",
            "tests.oauth.e2e.test_a",
            "test_b[/mcp//alias]",
        ),
        (
            "tests/oauth/e2e/test_a.py::test_b[read:user, gist]",
            "test_a.py::test_b[read:user, gist]",
            "tests.oauth.e2e.test_a",
            "test_b[read:user, gist]",
        ),
        (
            "tests/oauth/e2e/test_a.py::TestGroup::test_b[x.y]",
            "test_a.py::TestGroup::test_b[x.y]",
            "tests.oauth.e2e.test_a.TestGroup",
            "test_b[x.y]",
        ),
    ],
)
def test_node_and_junit_identities_map_to_the_same_case(nodeid, case, classname, name):
    assert case_id_from_nodeid(nodeid) == case
    assert case_id_from_nodeid(nodeid.replace("/", "\\", 3)) == case
    assert case_id_from_junit(classname, name) == case
    assert case_id_from_nodeid("tests/oauth/unit/test_a.py::test_b") is None
    assert case_id_from_junit("tests.unit.test_a", "test_b") is None


def test_refresh_expands_function_ids_and_keeps_exact_mappings():
    data = {
        "requirements": {
            "R1": {"description": "Function", "cases": ["f.py::test_v", "f.py::test_one"]},
            "R2": {"description": "Exact", "cases": ["f.py::test_one"]},
        },
        "platforms": {"f.py::test_v": ["nt"]},
        "external": {},
    }
    collected = ["f.py::test_v[a]", "f.py::test_v[b]", "f.py::test_one", "f.py::test_new"]
    refreshed, summary, follow_up = refresh_inventory(data, collected)
    assert refreshed["requirements"]["R1"]["cases"] == [
        "f.py::test_v[a]",
        "f.py::test_v[b]",
        "f.py::test_one",
    ]
    assert refreshed["requirements"]["R2"]["cases"] == ["f.py::test_one"]
    assert refreshed["platforms"] == {"f.py::test_v[a]": ["nt"], "f.py::test_v[b]": ["nt"]}
    assert refreshed["unmapped"] == ["f.py::test_new"]
    assert follow_up
    assert "removed: f.py::test_v" in summary
    assert "added: f.py::test_v[b] -> R1" in summary
    with pytest.raises(InventoryError, match="cases without requirement IDs"):
        parse_inventory(refreshed)
    # Once mapped, a second refresh changes nothing.
    refreshed["requirements"]["R2"]["cases"].append(refreshed.pop("unmapped")[0])
    parse_inventory(refreshed)
    again, summary, follow_up = refresh_inventory(refreshed, collected)
    assert (again, summary, follow_up) == (refreshed, [], False)


def test_refresh_reports_a_requirement_left_without_cases():
    data = {
        "requirements": {
            "R1": {"description": "Gone", "cases": ["f.py::test_gone"]},
            "R2": {"description": "Kept", "cases": ["f.py::test_kept"]},
        },
        "external": {},
    }
    refreshed, summary, follow_up = refresh_inventory(data, ["f.py::test_kept"])
    assert refreshed["requirements"]["R1"]["cases"] == []
    assert "empty: R1 has no collected cases" in summary
    assert follow_up


def test_write_inventory_uses_a_saved_full_collection(tmp_path, capsys):
    inventory = tmp_path / "requirements.json"
    collection = tmp_path / "collection.txt"
    inventory.write_text(
        json.dumps({"requirements": {"R1": {"description": "A", "cases": ["test_a.py::test_v"]}}}),
        encoding="utf-8",
    )
    collection.write_text(
        "tests/oauth/e2e/test_a.py::test_v[x]\ntests/oauth/e2e/test_a.py::test_v[y]\n\n"
        "2 tests collected in 0.01s\n",
        encoding="utf-8",
    )
    write_inventory(inventory, collection)
    assert load_inventory(inventory).cases == {"test_a.py::test_v[x]", "test_a.py::test_v[y]"}
    assert "Wrote 2 exact OAuth E2E case IDs" in capsys.readouterr().out
    collection.write_text("tests/oauth/e2e/test_a.py::test_w\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="Map every 'unmapped' case"):
        write_inventory(inventory, collection)


def test_repository_inventory_marks_complementary_shutdown_cases():
    inventory = load_inventory(INVENTORY)
    posix = "test_oauth_lifecycle.py::test_sigterm_drains_started_request_and_releases_state_lock"
    windows = "test_oauth_lifecycle.py::test_forced_termination_restarts_and_recovers_state_lock"
    assert inventory.platforms[posix] == {"posix"}
    assert inventory.platforms[windows] == {"nt"}
    assert {posix, windows} <= set(inventory.requirements["T20"]["cases"])
    assert all(case.startswith("test_oauth_") for case in inventory.cases)
