<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Dependency Contract

## Assertions

### `DEP-001` - PEP 621 owns every direct Python dependency

**Contract:** Root `pyproject.toml` MUST be the only direct Python dependency
manifest. Runtime dependencies MUST remain empty unless an ADR introduces a
reviewed runtime-lock policy. Quality, test, package, and documentation tools
MUST be exact direct pins to their latest stable releases compatible with their
supported job audience. Tool pins MUST remain disjoint. A platform compatibility
package MAY be shared only by the audiences that require it. A temporary older
pin MUST carry an inline reason and changelog entry.

**Evidence:**

- [`test_direct_dependencies_are_exact_and_scoped`](../../tests/unit/test_dependency_policy.py) - `tests/unit/test_dependency_policy.py::test_direct_dependencies_are_exact_and_scoped`
- [`test_dependabot_updates_python_and_actions_as_groups`](../../tests/unit/test_dependency_policy.py) - `tests/unit/test_dependency_policy.py::test_dependabot_updates_python_and_actions_as_groups`

### `DEP-002` - Exactly four non-empty generated hash locks exist

**Contract:** `requirements-dev.txt`, `requirements-test.txt`,
`requirements-package.txt`, and `requirements-docs.txt` MUST be the only Python
locks. Every entry MUST be an exact pin with one or more SHA-256 hashes. The
empty runtime dependency set MUST NOT have a placeholder `requirements.txt`.
Generated locks MUST NOT be hand-edited.

**Evidence:**

- [`test_all_committed_locks_are_pip_compile_hash_locks`](../../tests/unit/test_dependency_policy.py) - `tests/unit/test_dependency_policy.py::test_all_committed_locks_are_pip_compile_hash_locks`
- [`test_rejects_hashless_lock_entry`](../../tests/unit/test_dependency_snapshot.py) - `tests/unit/test_dependency_snapshot.py::test_rejects_hashless_lock_entry`

### `DEP-003` - Lock installation is hash-verified and wheel-only

**Contract:** Supported Make environments MUST install their exact lock with
`--require-hashes --only-binary=:all:` and run `pip check`. The isolated
resolver bootstrap MAY use one exact inline wheel-only install set; no workflow
MAY introduce an independent Python tool list.

**Evidence:**

- [`test_make_installs_locks_with_hashes_and_checks_drift`](../../tests/unit/test_dependency_policy.py) - `tests/unit/test_dependency_policy.py::test_make_installs_locks_with_hashes_and_checks_drift`

### `DEP-004` - Platform jobs install only their supported audience

**Contract:** Linux quality MAY install the dev lock containing Ruff, mypy,
Bandit, and pip-audit. Compatibility jobs on Windows and Linux MUST install only
the test lock; distribution jobs MUST install only the package lock; Pages MUST
install only the docs lock. A tool's missing wheel on an unrelated platform
MUST be handled by this audience split, not by a repository-wide downgrade.
Because pip-compile evaluates environment markers on its resolver host, the test
and package groups MUST directly pin their shared Windows console dependency.

**Evidence:**

- [`test_ci_preserves_project_specific_quality_and_platform_gates`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_ci_preserves_project_specific_quality_and_platform_gates`
- [`test_direct_dependencies_are_exact_and_scoped`](../../tests/unit/test_dependency_policy.py) - `tests/unit/test_dependency_policy.py::test_direct_dependencies_are_exact_and_scoped`

### `DEP-005` - Lock generation is reproducible and drift is blocking

**Contract:** `make lock` MUST regenerate all four locks from PEP 621 with the
exact resolver bootstrap. `make refresh-dependencies` MUST re-resolve after a
reviewed direct-pin update. `make freeze-check` MUST compile without upgrades
and fail on semantic lock drift.
`make lock-platform-check` MUST additionally prove that the test and package
locks can resolve exclusively from Windows wheels for every supported CPython
version, and the complete Linux CI contract MUST run that check.

**Evidence:**

- [`test_make_installs_locks_with_hashes_and_checks_drift`](../../tests/unit/test_dependency_policy.py) - `tests/unit/test_dependency_policy.py::test_make_installs_locks_with_hashes_and_checks_drift`

### `DEP-006` - Every non-empty lock is audited

**Contract:** The Linux CI contract MUST run strict vulnerability audit against
each of the four exact lock files. A known vulnerability MUST fail unless a
future exception mechanism is itself exact, reviewed, tested, and documented.

**Evidence:**

- [`test_make_installs_locks_with_hashes_and_checks_drift`](../../tests/unit/test_dependency_policy.py) - `tests/unit/test_dependency_policy.py::test_make_installs_locks_with_hashes_and_checks_drift`
- [`test_ci_preserves_project_specific_quality_and_platform_gates`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_ci_preserves_project_specific_quality_and_platform_gates`

### `DEP-007` - Dependency submission derives every lock offline

**Contract:** The snapshot generator MUST parse exactly the four hash locks,
cross-check direct pins against PEP 621, reject empty, hashless, missing, or
version-drifted content, and emit direct/transitive relationships without
network access or credentials.

**Evidence:**

- [`test_builds_all_exact_lock_manifests`](../../tests/unit/test_dependency_snapshot.py) - `tests/unit/test_dependency_snapshot.py::test_builds_all_exact_lock_manifests`
- [`test_rejects_direct_version_drift`](../../tests/unit/test_dependency_snapshot.py) - `tests/unit/test_dependency_snapshot.py::test_rejects_direct_version_drift`
- [`test_rejects_hashless_lock_entry`](../../tests/unit/test_dependency_snapshot.py) - `tests/unit/test_dependency_snapshot.py::test_rejects_hashless_lock_entry`
- [`test_rejects_unpinned_direct_dependency`](../../tests/unit/test_dependency_snapshot.py) - `tests/unit/test_dependency_snapshot.py::test_rejects_unpinned_direct_dependency`
