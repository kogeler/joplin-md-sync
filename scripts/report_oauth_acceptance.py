#!/usr/bin/env python3
"""Produce functional coverage evidence from actual pytest JUnit results.

This reports requirement coverage, not an assertion that every possible input
or every source branch was exercised. Packaging and systemd have separate gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def report(junit: Path, output: Path) -> None:
    matrix = json.loads((ROOT / "tests/oauth/e2e/requirements.json").read_text())
    tree = ET.parse(junit)
    outcomes = {}
    for case in tree.iter("testcase"):
        name = (
            case.attrib["classname"].rsplit(".", 1)[-1]
            + ".py::"
            + case.attrib["name"].split("[", 1)[0]
        )
        outcome = (
            "failed"
            if case.find("failure") is not None or case.find("error") is not None
            else ("skipped" if case.find("skipped") is not None else "passed")
        )
        outcomes.setdefault(name, []).append(outcome)
    requirements = {}
    for identity, requirement in matrix["requirements"].items():
        evidence = {node: outcomes.get(node, []) for node in requirement["tests"]}
        passed = all(
            cases and all(case == "passed" for case in cases) for cases in evidence.values()
        )
        requirements[identity] = {
            "description": requirement["description"],
            "status": "passed" if passed else "incomplete",
            "cases": evidence,
        }
    e2e = [
        case for name, cases in outcomes.items() if name.startswith("test_oauth_") for case in cases
    ]
    sources = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for pattern in ("src/joplin_md_sync/*.py", "tests/oauth/**/*.py", "scripts/*oauth*.py")
        for path in sorted(ROOT.glob(pattern))
    }
    data = {
        "functional_requirements": requirements,
        "passed_requirements": sum(row["status"] == "passed" for row in requirements.values()),
        "required_pytest_requirements": len(requirements),
        "e2e_cases": len(e2e),
        "passed_cases": e2e.count("passed"),
        "failed_cases": e2e.count("failed"),
        "skipped_cases": e2e.count("skipped"),
        "source_sha256": sources,
        "external_gates": matrix["external"],
        "live_github_claude": "not_run_requires_operator_credentials_and_browser",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + "\n")
    print(
        f"Functional OAuth E2E requirements: {data['passed_requirements']}/{len(requirements)}; cases: {data['passed_cases']}/{len(e2e)} passed; {data['failed_cases']} failed, {data['skipped_cases']} skipped"
    )
    if (
        data["passed_requirements"] != len(requirements)
        or data["failed_cases"]
        or data["skipped_cases"]
    ):
        raise SystemExit("OAuth acceptance evidence is incomplete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report(args.junit, args.output)
