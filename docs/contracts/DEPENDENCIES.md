<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Dependency Contract

## Assertions

### `DEP-001` - Standard project groups own development requirements

**Contract:** `pyproject.toml` MUST own development requirements through the
standard `dependency-groups` table: `quality`, `test`, `build`, and `docs`.
Published runtime dependencies MUST remain empty unless a reviewed runtime
dependency policy is introduced. Development groups MUST NOT become published
extras or runtime requirements. Build-backend requirements MUST remain in the
standard `build-system` table; release builds MUST use the locked build group.

**Evidence:**

- [`Project metadata`](../../pyproject.toml) - `configuration`
- [`build`](../../Makefile) - `make build`

### `DEP-002` - uv owns one universal lock

**Contract:** `uv.lock` MUST be the sole committed Python dependency lock.
Only uv or its native Dependabot integration MAY update it. Generated locks
MUST NOT be hand-edited, parsed by repository unit tests, or compared through
custom text/hash normalization code. Platform-specific transitive dependencies
MUST be resolved by uv from upstream metadata, not added as direct tools solely
to compensate for host-specific locking.

**Evidence:**

- [`lock-check`](../../Makefile) - `make lock-check`

### `DEP-003` - Environments install their committed dependency selection

**Contract:** Make environments MUST use `uv sync --locked` with the selected
Python interpreter and dedicated project-environment path. Tool environments
MUST select only their own group and require third-party wheels. CI MUST NOT
silently rewrite the lock or install an independently maintained tool list.
The reviewed CI uv executable version MUST have one owner in `.uv-version`;
the project minimum MUST NOT force Dependabot to use that exact CLI build.

**Evidence:**

- [`venv-dev`](../../Makefile) - `make venv-dev`
- [`venv-test`](../../Makefile) - `make venv-test`
- [`venv-package`](../../Makefile) - `make venv-package`
- [`venv-docs`](../../Makefile) - `make venv-docs`

### `DEP-004` - Platform jobs install only their supported group

**Contract:** Linux quality MUST select the quality group. Compatibility jobs
on Windows and Linux MUST select the test group; distribution jobs MUST select
the build group; Pages MUST select the docs group. Each supported platform and
CPython combination MUST install and execute its real tests rather than rely
on a locally simulated platform resolver.

**Evidence:**

- [`test_ci_preserves_project_specific_quality_and_platform_gates`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_ci_preserves_project_specific_quality_and_platform_gates`
- [`venv-test`](../../Makefile) - `make venv-test`
- [`venv-package`](../../Makefile) - `make venv-package`

### `DEP-005` - Lock validity is checked by its native manager

**Contract:** `make lock-check` MUST run `uv lock --check`. `make lock` MUST
run normal native locking, and `make refresh-dependencies` MUST explicitly
upgrade the complete graph within project requirements. A newly published
upstream release MUST NOT alone invalidate a committed compatible lock.
CI MUST NOT implement an additional definition of lock equivalence.

**Evidence:**

- [`lock-check`](../../Makefile) - `make lock-check`
- [`lock`](../../Makefile) - `make lock`
- [`refresh-dependencies`](../../Makefile) - `make refresh-dependencies`

### `DEP-006` - Every locked group is audited

**Contract:** Linux CI MUST audit all locked groups for known vulnerabilities.
The audit input MUST be a temporary native uv export of the committed lock,
not a second committed requirements file or a separately resolved environment.
Known vulnerabilities MUST remain blocking. Dependency review MUST remain in
the pull-request workflow.

**Evidence:**

- [`audit`](../../Makefile) - `make audit`
- [`test_ci_preserves_project_specific_quality_and_platform_gates`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_ci_preserves_project_specific_quality_and_platform_gates`

### `DEP-007` - GitHub uses its native dependency integration

**Contract:** Dependency graph ingestion, alerts, and security updates MUST
use GitHub's native uv support. Repository code MUST NOT parse the lock to
construct or submit a separate dependency snapshot. Hosted graph visibility
MUST be checked in GitHub after migration; local unit tests MUST NOT claim to
verify GitHub's hosted ingestion or scheduling.

**Evidence:**

- [`Dependabot`](../../.github/dependabot.yml) - `configuration`
- [`test_workflow_set_is_event_driven_and_every_action_is_sha_pinned`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_workflow_set_is_event_driven_and_every_action_is_sha_pinned`

### `DEP-008` - Dependabot is the only automatic update-branch writer

**Contract:** Dependabot MUST use the `uv` ecosystem for the root Python
project and include direct and transitive updates without package allowlists.
Python and Actions version updates MUST each form a group, allow at most one
open version-update PR, and enable automatic rebasing. Security groups MUST
remain separate from version groups and their weekly schedule. CI MUST validate
bot updates without repairing or pushing to bot branches.

**Evidence:**

- [`Dependabot`](../../.github/dependabot.yml) - `configuration`
- [`ci`](../../Makefile) - `make ci`

### `DEP-009` - Checkout environments are private to one host and user

**Contract:** Every Make environment MUST live under `.venvs/<key>/`, where
the key is derived privately from the validated OS machine identity and local
user. Raw machine identities MUST NOT be printed. An invalid identity MUST
stop Make without a shared fallback. Cleanup MUST preserve other hosts' and
users' environments and legacy root-level environments.

**Evidence:**

- [`test_environment_key_is_stable_private_and_scoped`](../../tests/unit/test_venv_root.py) - `tests/unit/test_venv_root.py::test_environment_key_is_stable_private_and_scoped`
- [`test_invalid_linux_identity_has_no_shared_fallback`](../../tests/unit/test_venv_root.py) - `tests/unit/test_venv_root.py::test_invalid_linux_identity_has_no_shared_fallback`
- [`test_make_selects_only_this_host_environments_and_fails_closed`](../../tests/unit/test_venv_root.py) - `tests/unit/test_venv_root.py::test_make_selects_only_this_host_environments_and_fails_closed`
- [`test_make_clean_preserves_other_host_and_legacy_environments`](../../tests/unit/test_venv_root.py) - `tests/unit/test_venv_root.py::test_make_clean_preserves_other_host_and_legacy_environments`

### `DEP-010` - Updates are accepted by real product checks

**Contract:** Dependency updates MUST pass the same application, live Joplin,
platform, packaging, documentation, quality, and security checks as other
changes. Repository CI MUST NOT test a vendored or containerized Dependabot
implementation or assert the internal representation of generated locks.
Real incompatibilities MUST remain failures; native integration does not
promise that every dependency release is compatible.

**Evidence:**

- [`ci`](../../Makefile) - `make ci`
- [`test_ci_preserves_project_specific_quality_and_platform_gates`](../../tests/unit/test_ci_policy.py) - `tests/unit/test_ci_policy.py::test_ci_preserves_project_specific_quality_and_platform_gates`
