"""Regression tests for the complete least-privilege CI/CD contract."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION_REFERENCE = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
SHA_REFERENCE = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _cache_dependency_paths(workflow: str) -> list[list[str]]:
    blocks = []
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "cache-dependency-path: |":
            continue
        indent = len(line) - len(line.lstrip()) + 2
        entries = []
        for entry in lines[index + 1 :]:
            if not entry.strip() or len(entry) - len(entry.lstrip()) != indent:
                break
            entries.append(entry.strip())
        blocks.append(entries)
    return blocks


def _assigned_string(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, str), (path, name)
            return value
    raise AssertionError((path, name))


def test_tests_do_not_duplicate_owned_version_pins() -> None:
    ci = _workflow("ci.yml")
    live_runtime = ROOT / "tests_live" / "ephemeral_joplin.py"
    owned = {
        (ROOT / ".version").read_text(encoding="utf-8").strip(),
        _assigned_string(ROOT / "src" / "joplin_md_sync" / "mcp_server.py", "MCP_PROTOCOL_VERSION"),
        _assigned_string(ROOT / "src" / "joplin_md_sync" / "gpt_openapi.py", "OPENAPI_VERSION"),
        _assigned_string(live_runtime, "JOPLIN_VERSION"),
        _assigned_string(live_runtime, "JOPLIN_DEB_SHA256"),
        *(
            reference
            for reference in ACTION_REFERENCE.findall(
                "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOWS.glob("*.yml"))
            )
            if not reference.startswith("./")
        ),
        *(
            line.strip()
            for path in ROOT.glob("requirements*.in")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ),
        *re.findall(r'^\s+python(?:-version)?:\s+"([^"]+)"$', ci, re.MULTILINE),
    }
    test_paths = [
        *sorted((ROOT / "tests").rglob("*.py")),
        *sorted((ROOT / "tests_live").glob("test_*.py")),
        *sorted((ROOT / "scripts" / "joplin_terminal_service" / "tests").glob("test_*.py")),
    ]
    for path in test_paths:
        content = path.read_text(encoding="utf-8")
        duplicated = sorted(value for value in owned if value and value in content)
        assert not duplicated, (path, duplicated)


def test_workflow_set_is_event_driven_and_every_action_is_sha_pinned() -> None:
    assert {path.name for path in WORKFLOWS.glob("*.yml")} == {
        "ci.yml",
        "dependency-submission.yml",
        "pages.yml",
        "pr-body.yml",
        "release.yml",
    }
    for path in WORKFLOWS.glob("*.yml"):
        content = path.read_text(encoding="utf-8")
        assert "permissions:" in content, path
        assert "concurrency:" in content, path
        for reference in ACTION_REFERENCE.findall(content):
            if reference.startswith("./"):
                continue
            assert SHA_REFERENCE.fullmatch(reference), (path, reference)
        assert "persist-credentials: true" not in content


def test_codeowners_assigns_entire_repository_to_maintainer() -> None:
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    rules = [line for line in codeowners.splitlines() if line and not line.startswith("#")]
    assert rules == ["* @kogeler"]


def test_ci_preserves_project_specific_quality_and_platform_gates() -> None:
    ci = _workflow("ci.yml")
    for job in (
        "quality:",
        "live-joplin:",
        "compatibility:",
        "distribution:",
        "dependency-review:",
        "codeql:",
        "version:",
    ):
        assert job in ci
    for contract in (
        "make ci PY=python",
        "python-version: ${{ matrix.python }}",
        "platform: linux",
        "arch: arm64",
        "windows-latest",
        "make test-service-installer",
        "name: Live protocols",
        "make test-live PY=python",
        "command -v Xvfb",
        "actions/dependency-review-action@",
        "github/codeql-action/init@",
        "github/codeql-action/analyze@",
        "make standalone checksums smoke-standalone verify-release",
    ):
        assert contract in ci
    assert "security-events: write" in ci
    assert "pull-requests: write" not in ci
    assert "contents: write" not in ci
    assert "cache-dependency-path: requirements" not in ci
    audiences = [
        [entry.removesuffix(".txt") for entry in block if entry.endswith(".txt")]
        for block in _cache_dependency_paths(ci)
    ]
    assert ["requirements-test"] in audiences
    assert ["requirements-package"] in audiences
    for name in ("ci.yml", "pages.yml", "release.yml"):
        for block in _cache_dependency_paths(_workflow(name)):
            assert block
            assert sorted(block) == sorted(
                f"{stem}{suffix}"
                for stem in {entry.rsplit(".", 1)[0] for entry in block}
                for suffix in (".in", ".txt")
            ), (name, block)
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "COVERAGE_MIN ?= 87" in makefile
    assert "--cov-fail-under=$(COVERAGE_MIN)" in makefile


def test_live_ci_uses_checksum_verified_ephemeral_joplin_binary() -> None:
    runtime = (ROOT / "tests_live" / "ephemeral_joplin.py").read_text(encoding="utf-8")
    live_tests = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "tests_live").glob("test_*.py")
    )
    assert re.search(r'^JOPLIN_VERSION = "[^"]+"$', runtime, re.MULTILINE)
    assert "Joplin-{JOPLIN_VERSION}.deb" in runtime
    assert re.search(
        r'^JOPLIN_DEB_SHA256 = "[0-9a-f]{64}"$',
        runtime,
        re.MULTILINE,
    )
    assert 'TemporaryDirectory(prefix="jms-live-joplin-", dir="/tmp")' in runtime
    assert "dpkg-deb" in runtime
    assert "Xvfb" in runtime
    assert "npm install" not in runtime
    assert 'shutil.which("node")' not in runtime
    assert 'REPO / "token"' not in live_tests


def test_version_job_compares_exact_base_and_head() -> None:
    ci = _workflow("ci.yml")
    assert "github.event.pull_request.base.sha" in ci
    assert "github.event.pull_request.head.repo.full_name" in ci
    assert "github.event.pull_request.head.sha" in ci
    assert "unpublished_base_version" in ci
    assert "python scripts/check_version_increment.py --base-version" in ci
    version_job = ci.split("\n  version:\n", 1)[1]
    assert "    name: Version increment\n" in version_job
    assert "published_current_version" in version_job
    assert "!current.data.draft && !current.data.prerelease" in version_job
    changed = version_job.index('if [[ "$current_version" != "$base_version" ]]; then')
    unpublished = version_job.index('elif [[ "$PUBLISHED_CURRENT_VERSION" != "true" ]]; then')
    maintenance = version_job.index(
        'check_version_increment.py --published-version "$current_version"'
    )
    assert changed < unpublished < maintenance


def _version_comparison_script() -> str:
    job = _workflow("ci.yml").split("\n  version:\n", 1)[1]
    step = job.split("- name: Require an increase for a changed or unpublished version\n", 1)[1]
    lines = step.split("        run: |\n", 1)[1].splitlines()
    body = []
    for line in lines:
        if line.strip() and not line.startswith("          "):
            break
        body.append(line.removeprefix("          "))
    return "\n".join(body).strip() + "\n"


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="the workflow step runs in Linux bash",
)
@pytest.mark.parametrize(
    ("base", "current", "published", "unpublished_base", "code", "message"),
    (
        ("1.2.3", "1.2.3", "true", "", 0, "1.2.3 is already published; no increment required"),
        ("1.2.3", "1.2.3", "false", "1.2.2", 0, "incremented: 1.2.2 -> 1.2.3"),
        ("1.2.3", "1.3.0", "true", "", 0, "incremented: 1.2.3 -> 1.3.0"),
        ("1.2.3", "1.2.2", "false", "1.2.1", 2, "must be incremented"),
        ("1.2.3", "1.2.3", "false", "1.2.3", 2, "must be incremented"),
    ),
)
def test_version_job_accepts_published_maintenance_and_requires_other_increases(
    tmp_path: Path,
    base: str,
    current: str,
    published: str,
    unpublished_base: str,
    code: int,
    message: str,
) -> None:
    (tmp_path / "base").mkdir()
    (tmp_path / "base" / ".version").write_text(f"{base}\n", encoding="utf-8")
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / ".version").write_text(f"{current}\n", encoding="utf-8")
    shutil.copy2(ROOT / "scripts" / "check_version_increment.py", source / "scripts")
    shim = tmp_path / "bin" / "python"
    shim.parent.mkdir()
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    step = tmp_path / "step.sh"
    step.write_text(_version_comparison_script(), encoding="utf-8")

    result = subprocess.run(
        ["bash", "-e", str(step)],
        cwd=source,
        env={
            **os.environ,
            "PATH": f"{shim.parent}{os.pathsep}{os.environ['PATH']}",
            "PUBLISHED_CURRENT_VERSION": published,
            "UNPUBLISHED_BASE_VERSION": unpublished_base,
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == code, result.stderr
    assert message in result.stdout + result.stderr


def test_published_release_makes_later_main_pushes_a_no_op() -> None:
    release = _workflow("release.yml")
    trigger = release.split("\npermissions:", 1)[0]
    assert "paths:" not in trigger
    state = release.split("\n  release-state:\n", 1)[1].split("\n  ci:\n", 1)[0]
    assert "const published = Boolean(existing && !existing.data.draft);" in state
    assert "if (!published && commit && commit !== context.sha)" in state
    assert "Published release ${tagName} has no tag" in state
    assert "commit !== existing.data.target_commitish" in state
    assert "if (published && commit !== context.sha)" in state
    gate = release.split("\n  ci:\n", 1)[1].split("\n  build-python-distributions:", 1)[0]
    assert "needs: release-state" in gate
    assert "needs.release-state.outputs.release_required == 'true'" in gate
    assert "needs.release-state.outputs.pypi_required == 'true'" in gate


def test_dependency_submission_is_a_separate_trusted_write_boundary() -> None:
    workflow = _workflow("dependency-submission.yml")
    assert "push:" in workflow
    assert "pull_request" not in workflow
    assert workflow.count("contents: write") == 1
    assert "make dependency-snapshot" in workflow
    assert "POST /repos/{owner}/{repo}/dependency-graph/snapshots" in workflow
    assert "requirements-docs.txt" in workflow
    assert "requirements-test.txt" in workflow
    assert "requirements-package.txt" in workflow
    assert "Submit all four lock manifests" in workflow
    assert "joplin-md-sync-pip-locks" in workflow


def test_release_reuses_ci_and_writes_only_in_publish_job() -> None:
    release = _workflow("release.yml")
    assert "uses: ./.github/workflows/ci.yml" in release
    assert "release-state:" in release
    assert "publish:" in release
    assert release.count("contents: write") == 1
    assert "make build smoke-wheel smoke-sdist" in release
    assert "make zipapp" in release
    assert "actions/download-artifact@" in release
    assert "draft: true" in release
    assert "github.rest.repos.uploadReleaseAsset" in release
    assert "uploaded.status !== 201" in release
    assert '"POST /repos/{owner}/{repo}/releases/{release_id}/assets{?name}"' not in release
    assert "deleteReleaseAsset" in release
    assert "softprops/" not in release
    assert "git push" not in release
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "SOURCE_DATE_EPOCH" in makefile
    assert "scripts/normalize_sdist.py" in makefile


def test_pypi_publication_uses_oidc_and_verified_shared_artifacts() -> None:
    release = _workflow("release.yml")
    assert "pypi_required" in release
    assert "https://pypi.org/pypi/" in release
    assert "file.yanked !== false" in release
    assert "build-python-distributions:" in release
    assert "publish-pypi:" in release
    assert "name: python-distributions" in release
    assert "verify_pypi_release.py --dist-dir dist" in release
    assert "environment:\n      name: pypi" in release
    assert release.count("id-token: write") == 1
    assert "pypa/gh-action-pypi-publish@" in release
    assert "packages-dir: dist" in release
    assert 'attestations: "true"' in release
    assert "needs.publish-pypi.result == 'success'" in release
    assert release.index("Publish with PyPI Trusted Publishing") < release.index(
        "Create exact-version release and upload assets"
    )
    assert "skip-existing" not in release
    assert "PYPI_TOKEN" not in release
    assert "password:" not in release
    assert "secrets." not in release


def test_pages_validates_prs_and_confines_publish_permissions() -> None:
    pages = _workflow("pages.yml")
    assert "pull_request:" in pages
    assert '"docs/**"' in pages
    assert "make docs-audit" in pages
    assert "site/sitemap.xml" not in pages
    assert pages.count("pages: write") == 1
    assert pages.count("id-token: write") == 1
    assert "github.event_name != 'pull_request'" in pages
    trigger = pages.split("\npermissions:", 1)[0]
    assert '"requirements-docs.in"' in trigger
    assert '"requirements-docs.txt"' in trigger


def test_pr_body_is_the_only_pull_request_target_write_boundary() -> None:
    workflow = _workflow("pr-body.yml")
    all_workflows = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOWS.glob("*.yml"))
    assert all_workflows.count("pull_request_target:") == 1
    assert all_workflows.count("pull-requests: write") == 1
    assert "github.rest.repos.getContent" in workflow
    assert "github.rest.pulls.update" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" not in workflow
    assert "secrets." not in workflow
    trigger, _, jobs = workflow.partition("\njobs:\n")
    assert "paths:\n      - CHANGELOG.md" in trigger
    assert "types:\n      - opened\n      - reopened\n      - synchronize" in trigger
    assert "permissions:\n  contents: read\n" in trigger
    assert "pull-requests: write" not in trigger
    assert "    permissions:\n      contents: read\n      pull-requests: write\n" in jobs
    assert 'Buffer.from(file.content.replace(/\\n/g, ""), "base64")' in jobs
    assert "changelog.byteLength > 1_000_000" in jobs
    assert "refusing overwrite" in jobs
    checkout = jobs.split("- name: Check out trusted default branch", 1)[1].split(
        "\n      - name:", 1
    )[0]
    assert "persist-credentials: false" in checkout
    assert "ref:" not in checkout
