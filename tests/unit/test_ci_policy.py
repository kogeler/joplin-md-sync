"""Regression tests for the complete least-privilege CI/CD contract."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION_REFERENCE = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
SHA_REFERENCE = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


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
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
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
            requirement
            for group in project["optional-dependencies"].values()
            for requirement in group
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
    assert "cache-dependency-path: requirements-test.txt" in ci
    assert "cache-dependency-path: requirements-package.txt" in ci
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


def test_pr_body_is_the_only_pull_request_target_write_boundary() -> None:
    workflow = _workflow("pr-body.yml")
    all_workflows = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOWS.glob("*.yml"))
    assert all_workflows.count("pull_request_target:") == 1
    assert all_workflows.count("pull-requests: write") == 1
    assert "github.rest.repos.getContent" in workflow
    assert "github.rest.pulls.update" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" not in workflow
    assert "secrets." not in workflow
