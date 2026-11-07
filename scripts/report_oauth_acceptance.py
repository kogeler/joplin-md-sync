#!/usr/bin/env python3
"""Verify OAuth E2E acceptance evidence against the exact case inventory.

``tests/oauth/e2e/requirements.json`` maps every functional requirement to
exact pytest case IDs, ``file.py::function[parameter-id]``. The collection gate
in ``tests/conftest.py`` and this report compare those IDs exactly. The report
covers functional requirements from actual JUnit results; it does not assert
that every input or source branch was exercised. Packaging and systemd have
separate gates.

After an intentional test change, refresh the inventory deliberately from a
full collection with ``make oauth-e2e-inventory`` (this script with
``--write-inventory`` in the test environment), then review the diff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "tests/oauth/e2e/requirements.json"
E2E_DIRECTORY = ("oauth", "e2e")
# ``os.name`` values. Only genuinely platform-specific cases declare them.
PLATFORMS = frozenset({"posix", "nt"})
ACCEPTED = frozenset({"passed", "not_applicable"})
REFRESH_HINT = "refresh it deliberately with `make oauth-e2e-inventory` and review the diff"
XPASS_MESSAGE = "xfail-marked test passes unexpectedly"


class InventoryError(ValueError):
    """The exact case inventory is malformed or needs a deliberate update."""


class EvidenceError(ValueError):
    """JUnit evidence is missing, malformed or incomplete."""


def case_id_from_nodeid(nodeid: str) -> str | None:
    """Return ``file.py::name[params]`` for a node directly under ``oauth/e2e``."""
    path, separator, names = nodeid.partition("::")
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if not separator or not names or parts[-3:-1] != E2E_DIRECTORY:
        return None
    return parts[-1] + "::" + names


def case_id_from_junit(classname: str, name: str) -> str | None:
    """Invert pytest's JUnit ``classname``/``name`` mangling for E2E cases."""
    parts = classname.split(".")
    for index in range(len(parts) - 2):
        if tuple(parts[index : index + 2]) == E2E_DIRECTORY:
            module, classes = parts[index + 2], parts[index + 3 :]
            return module + ".py::" + "::".join([*classes, name])
    return None


def function_of(case: str) -> str:
    """Return the case ID without its parameter ID."""
    return case.split("[", 1)[0]


def valid_case_id(case: object) -> bool:
    if not isinstance(case, str):
        return False
    module, separator, name = case.partition("::")
    return bool(
        separator
        and name
        and module.endswith(".py")
        and "/" not in module
        and "\\" not in module
        and case == case.strip()
    )


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = Counter(key for key, _ in pairs)
    duplicated = sorted(key for key, count in keys.items() if count > 1)
    if duplicated:
        raise InventoryError("Conflicting duplicate inventory keys: " + ", ".join(duplicated))
    return dict(pairs)


def read_inventory_data(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except FileNotFoundError:
        raise InventoryError(f"OAuth E2E inventory not found: {path}") from None
    except json.JSONDecodeError as error:
        raise InventoryError(f"Malformed OAuth E2E inventory {path}: {error}") from None
    if not isinstance(data, dict) or not isinstance(data.get("requirements"), dict):
        raise InventoryError("OAuth E2E inventory needs a 'requirements' object")
    return data


@dataclass(frozen=True)
class Inventory:
    requirements: Mapping[str, Mapping[str, Any]]
    platforms: Mapping[str, frozenset[str]]
    external: Mapping[str, Any]

    @cached_property
    def cases(self) -> frozenset[str]:
        return frozenset(
            case for requirement in self.requirements.values() for case in requirement["cases"]
        )

    def applicable(self, case: str, platform: str) -> bool:
        allowed = self.platforms.get(case)
        return allowed is None or platform in allowed


def parse_inventory(data: Mapping[str, Any]) -> Inventory:
    """Validate inventory data and return it, or raise one complete error."""
    problems = []
    unknown = sorted(set(data) - {"requirements", "platforms", "unmapped", "external"})
    if unknown:
        problems.append("unknown inventory keys: " + ", ".join(unknown))
    requirements: dict[str, dict[str, Any]] = {}
    raw_requirements = data.get("requirements")
    if not isinstance(raw_requirements, dict) or not raw_requirements:
        problems.append("'requirements' must be a non-empty object")
        raw_requirements = {}
    for identity, requirement in raw_requirements.items():
        if not isinstance(requirement, dict) or set(requirement) != {"description", "cases"}:
            problems.append(f"{identity} must contain exactly 'description' and 'cases'")
            continue
        cases = requirement["cases"]
        if not isinstance(requirement["description"], str) or not requirement["description"]:
            problems.append(f"{identity} needs a description")
        if not isinstance(cases, list) or not cases:
            problems.append(f"{identity} must list at least one exact case ID")
            continue
        invalid = [case for case in cases if not valid_case_id(case)]
        if invalid:
            problems.append(f"{identity} has malformed case IDs: {invalid!r}")
            continue
        duplicates = sorted(case for case, count in Counter(cases).items() if count > 1)
        if duplicates:
            problems.append(f"{identity} lists duplicate cases: " + ", ".join(duplicates))
        requirements[identity] = {"description": requirement["description"], "cases": tuple(cases)}
    known = {case for requirement in requirements.values() for case in requirement["cases"]}
    platforms: dict[str, frozenset[str]] = {}
    raw_platforms = data.get("platforms", {})
    if not isinstance(raw_platforms, dict):
        problems.append("'platforms' must map case IDs to platform lists")
        raw_platforms = {}
    for case, allowed in raw_platforms.items():
        if case not in known:
            problems.append(f"platform entry for a case no requirement lists: {case}")
        if (
            not isinstance(allowed, list)
            or not allowed
            or len(set(allowed)) != len(allowed)
            or not set(allowed) <= PLATFORMS
        ):
            problems.append(
                f"platforms for {case} must be distinct values from {sorted(PLATFORMS)}"
            )
            continue
        platforms[case] = frozenset(allowed)
    unmapped = data.get("unmapped", [])
    if unmapped:
        problems.append(
            "cases without requirement IDs (move each from 'unmapped' into the 'cases' of "
            "every requirement it proves): " + ", ".join(map(str, unmapped))
        )
    external = data.get("external", {})
    if not isinstance(external, dict):
        problems.append("'external' must be an object")
        external = {}
    if problems:
        raise InventoryError("Invalid OAuth E2E inventory: " + "; ".join(problems))
    return Inventory(requirements, platforms, external)


def load_inventory(path: Path = INVENTORY) -> Inventory:
    return parse_inventory(read_inventory_data(path))


def collection_problems(inventory: Inventory, nodeids: Iterable[str]) -> list[str]:
    """Compare collected E2E case IDs exactly with the inventory."""
    collected = Counter(case for case in map(case_id_from_nodeid, nodeids) if case is not None)
    problems = []
    missing = sorted(inventory.cases - collected.keys())
    if missing:
        problems.append("Required OAuth E2E cases were not collected: " + ", ".join(missing))
    unlisted = sorted(collected.keys() - inventory.cases)
    if unlisted:
        problems.append(
            "Collected OAuth E2E cases are not in the exact inventory ("
            + REFRESH_HINT
            + "): "
            + ", ".join(unlisted)
        )
    duplicates = sorted(case for case, count in collected.items() if count > 1)
    if duplicates:
        problems.append("OAuth E2E cases were collected more than once: " + ", ".join(duplicates))
    return problems


def testcase_outcome(case: ET.Element) -> str:
    tags = {child.tag for child in case}
    if "failure" in tags:
        return "failed"
    if "error" in tags:
        return "error"
    skipped = case.find("skipped")
    if skipped is not None:
        if skipped.get("type") == "pytest.xfail":
            return "xfail"
        if skipped.get("message") == XPASS_MESSAGE:
            return "xpass"
        return "skipped"
    return "passed"


def junit_records(path: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Return outcome records per E2E case and E2E collection failures."""
    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError:
        raise EvidenceError(f"JUnit results not found: {path}") from None
    except (ET.ParseError, OSError) as error:
        raise EvidenceError(f"Malformed JUnit XML {path}: {error}") from None
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = root.findall("testsuite")
    else:
        raise EvidenceError(f"Malformed JUnit XML {path}: unexpected root <{root.tag}>")
    if not suites:
        raise EvidenceError(f"Incomplete JUnit XML {path}: no test suite")
    records: dict[str, list[str]] = {}
    collection_failures = []
    for suite in suites:
        cases = suite.findall("testcase")
        counts = {
            "tests": len(cases),
            "failures": sum(len(case.findall("failure")) for case in cases),
            "errors": sum(len(case.findall("error")) for case in cases),
            "skipped": sum(len(case.findall("skipped")) for case in cases),
        }
        for attribute, expected in counts.items():
            value = suite.get(attribute)
            # unittest subTest reports add to the declared total without their
            # own <testcase> elements; outcome counts must still match exactly.
            if (
                value is None
                or not value.isdigit()
                or (int(value) < expected if attribute == "tests" else int(value) != expected)
            ):
                raise EvidenceError(
                    f"Incomplete JUnit XML {path}: suite declares {attribute}={value!r} "
                    f"but contains {expected}"
                )
        for case in cases:
            classname, name = case.get("classname"), case.get("name")
            if classname is None or not name:
                raise EvidenceError(f"Malformed JUnit XML {path}: testcase without identity")
            identity = case_id_from_junit(classname, name)
            if identity is not None:
                records.setdefault(identity, []).append(testcase_outcome(case))
            elif not classname and ".".join(E2E_DIRECTORY) + "." in name:
                collection_failures.append(name)
    return records, collection_failures


def case_status(inventory: Inventory, case: str, outcomes: list[str], platform: str) -> str:
    if not outcomes:
        return "missing"
    if len(outcomes) > 1:
        return "duplicate"
    if inventory.applicable(case, platform):
        return outcomes[0]
    # A case for another platform is skipped here and can never count.
    return "not_applicable" if outcomes[0] == "skipped" else "ran_on_non_applicable_platform"


def evaluate(
    inventory: Inventory,
    records: Mapping[str, list[str]],
    collection_failures: Iterable[str],
    platform: str,
) -> dict[str, Any]:
    statuses = {
        case: case_status(inventory, case, records.get(case, []), platform)
        for case in sorted(inventory.cases)
    }
    requirements = {}
    for identity, requirement in inventory.requirements.items():
        cases = {case: statuses[case] for case in requirement["cases"]}
        if any(status not in ACCEPTED for status in cases.values()):
            status = "incomplete"
        elif any(inventory.applicable(case, platform) for case in cases):
            status = "passed"
        else:
            status = "not_applicable"
        requirements[identity] = {
            "description": requirement["description"],
            "status": status,
            "cases": cases,
            "missing": [case for case, value in cases.items() if value == "missing"],
        }
    unlisted = sorted(set(records) - inventory.cases)
    duplicates = sorted(case for case, outcomes in records.items() if len(outcomes) > 1)
    errors = [f"OAuth E2E collection failed: {name}" for name in collection_failures]
    if unlisted:
        errors.append(f"JUnit E2E cases are not in the exact inventory ({REFRESH_HINT})")
    if duplicates:
        errors.append("JUnit contains duplicate or conflicting E2E records")
    outcomes = [
        outcome
        for case, values in records.items()
        for outcome in values
        if case not in inventory.cases or inventory.applicable(case, platform)
    ]
    return {
        "platform": platform,
        "evidence_errors": errors,
        "functional_requirements": requirements,
        "passed_requirements": sum(row["status"] == "passed" for row in requirements.values()),
        "not_applicable_requirements": sum(
            row["status"] == "not_applicable" for row in requirements.values()
        ),
        "required_pytest_requirements": len(requirements),
        "inventory_cases": len(statuses),
        "e2e_cases": sum(len(values) for values in records.values()),
        "passed_cases": outcomes.count("passed"),
        "failed_cases": outcomes.count("failed") + outcomes.count("error"),
        "skipped_cases": sum(outcome in {"skipped", "xfail", "xpass"} for outcome in outcomes),
        "not_applicable_cases": [
            case for case, value in statuses.items() if value == "not_applicable"
        ],
        "missing_cases": [case for case, value in statuses.items() if value == "missing"],
        "rejected_cases": {
            case: value for case, value in statuses.items() if value not in ACCEPTED | {"missing"}
        },
        "unlisted_cases": unlisted,
        "duplicate_cases": duplicates,
        "external_gates": dict(inventory.external),
        "live_github_claude": "not_run_requires_operator_credentials_and_browser",
    }


def source_hashes() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for pattern in (
            "src/joplin_md_sync/*.py",
            "tests/oauth/**/*.py",
            "tests/oauth/e2e/*.json",
            "scripts/*oauth*.py",
        )
        for path in sorted(ROOT.glob(pattern))
    }


def report(
    junit: Path,
    output: Path,
    *,
    inventory_path: Path = INVENTORY,
    platform: str = os.name,
) -> None:
    """Write evidence JSON, then fail unless every applicable required case passed."""
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        inventory = load_inventory(inventory_path)
        records, collection_failures = junit_records(junit)
    except (InventoryError, EvidenceError) as error:
        data = {
            "platform": platform,
            "evidence_errors": [str(error)],
            "source_sha256": source_hashes(),
        }
        output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(f"OAuth acceptance evidence is invalid: {error}") from None
    data = evaluate(inventory, records, collection_failures, platform)
    data["source_sha256"] = source_hashes()
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    requirements = data["functional_requirements"]
    print(
        f"Functional OAuth E2E requirements on {platform}: {data['passed_requirements']}"
        f"/{len(requirements)} passed, {data['not_applicable_requirements']} not applicable; "
        f"cases: {data['passed_cases']}/{data['e2e_cases']} passed; "
        f"{data['failed_cases']} failed, {data['skipped_cases']} skipped, "
        f"{len(data['missing_cases'])} missing, {len(data['not_applicable_cases'])} "
        "for another platform"
    )
    for label in ("missing_cases", "unlisted_cases", "duplicate_cases"):
        for case in data[label]:
            print(f"{label.removesuffix('_cases')}: {case}", file=sys.stderr)
    for case, status in data["rejected_cases"].items():
        print(f"{status}: {case}", file=sys.stderr)
    if data["evidence_errors"] or any(
        row["status"] not in ACCEPTED for row in requirements.values()
    ):
        raise SystemExit(
            "OAuth acceptance evidence is incomplete: "
            + "; ".join(data["evidence_errors"] or ["required cases did not all pass"])
        )


def refresh_inventory(
    data: Mapping[str, Any], collected: Iterable[str]
) -> tuple[dict[str, Any], list[str], bool]:
    """Return the inventory for exactly ``collected`` cases, a summary and follow-up need.

    Existing mappings stay exact. A newly collected case joins every requirement
    that already lists a case of the same function (or that function's former
    unparameterized ID) and inherits a platform entry shared by those cases.
    Other new cases go to ``unmapped`` for a deliberate requirement mapping.
    """
    order: dict[str, int] = {}
    for case in collected:
        order.setdefault(case, len(order))
    requirements = data["requirements"]
    platforms = data.get("platforms", {})
    previous = [case for requirement in requirements.values() for case in requirement["cases"]]
    previous_unmapped = list(data.get("unmapped", []))
    known = set(previous) | set(previous_unmapped)
    summary = [f"removed: {case}" for case in sorted(known - order.keys())]
    new = sorted(order.keys() - known, key=order.__getitem__)
    refreshed: dict[str, Any] = {}
    for identity, requirement in requirements.items():
        functions = {function_of(case) for case in requirement["cases"]}
        cases = {case for case in requirement["cases"] if case in order}
        for case in new:
            if function_of(case) in functions:
                cases.add(case)
                summary.append(f"added: {case} -> {identity}")
        if not cases:
            summary.append(f"empty: {identity} has no collected cases")
        refreshed[identity] = {
            "description": requirement["description"],
            "cases": sorted(cases, key=order.__getitem__),
        }
    mapped = {case for requirement in refreshed.values() for case in requirement["cases"]}
    unmapped = sorted(set(order) - mapped, key=order.__getitem__)
    summary.extend(f"unmapped: {case}" for case in unmapped)
    new_platforms = {case: list(allowed) for case, allowed in platforms.items() if case in mapped}
    for case in (case for case in new if case in mapped):
        inherited = {
            tuple(allowed)
            for sibling, allowed in platforms.items()
            if function_of(sibling) == function_of(case)
        }
        if len(inherited) == 1:
            new_platforms[case] = list(inherited.pop())
            summary.append(f"platform: {case} -> {new_platforms[case]}")
    result: dict[str, Any] = {"requirements": refreshed}
    if new_platforms:
        result["platforms"] = dict(sorted(new_platforms.items(), key=lambda item: order[item[0]]))
    if unmapped:
        result["unmapped"] = unmapped
    result["external"] = data.get("external", {})
    follow_up = bool(unmapped) or any(not row["cases"] for row in refreshed.values())
    return result, summary, follow_up


def collect_case_ids() -> list[str]:
    """Collect every E2E case ID, in collection order, with the current interpreter."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/oauth/e2e",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(
            "OAuth E2E collection failed; run `make oauth-e2e-inventory` in the test "
            "environment:\n" + completed.stdout[-4000:] + completed.stderr[-4000:]
        )
    return [case for case in map(case_id_from_nodeid, completed.stdout.splitlines()) if case]


def write_inventory(path: Path, collection: Path | None) -> None:
    try:
        data = read_inventory_data(path)
    except InventoryError as error:
        raise SystemExit(str(error)) from None
    collected = (
        [
            case
            for case in map(
                case_id_from_nodeid, collection.read_text(encoding="utf-8").splitlines()
            )
            if case
        ]
        if collection is not None
        else collect_case_ids()
    )
    if not collected:
        raise SystemExit("No OAuth E2E cases were collected; the inventory was not changed")
    refreshed, summary, follow_up = refresh_inventory(data, collected)
    path.write_text(json.dumps(refreshed, indent=2) + "\n", encoding="utf-8")
    for line in summary:
        print(line)
    print(f"Wrote {len(set(collected))} exact OAuth E2E case IDs to {path}")
    if follow_up:
        raise SystemExit(
            "Map every 'unmapped' case to the requirements it proves (and list genuinely "
            "platform-specific cases under 'platforms'), keep every requirement non-empty, "
            "then run `make oauth-e2e-inventory` again"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--junit", type=Path, help="pytest JUnit XML results to verify")
    mode.add_argument(
        "--write-inventory",
        action="store_true",
        help="deliberately rewrite the inventory from a full E2E collection",
    )
    parser.add_argument("--output", type=Path, help="evidence JSON to write")
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument(
        "--platform",
        choices=sorted(PLATFORMS),
        default=os.name,
        help="platform whose applicable cases must pass (default: this host)",
    )
    parser.add_argument(
        "--collection",
        type=Path,
        help="pytest --collect-only -q output to use instead of collecting",
    )
    args = parser.parse_args(argv)
    if args.write_inventory:
        write_inventory(args.inventory, args.collection)
        return
    if args.output is None:
        parser.error("--output is required with --junit")
    report(args.junit, args.output, inventory_path=args.inventory, platform=args.platform)


if __name__ == "__main__":
    main()
