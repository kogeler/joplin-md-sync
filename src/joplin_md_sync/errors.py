"""Error hierarchy, stable exit codes, and machine-readable result codes.

Exit codes are part of the public agent contract (see AGENTS.md and
docs/CLI.md) and must stay stable across releases.
"""

from __future__ import annotations

# --- Stable exit codes (public contract) -----------------------------------

EXIT_OK = 0
EXIT_DIFF = 1  # differences or pending actions found
EXIT_CONFLICTS = 2  # unresolved conflicts found
EXIT_INVALID_WORKSPACE = 3  # invalid local workspace or malformed managed file
EXIT_API = 4  # Joplin API unavailable or authentication failed
EXIT_CONCURRENT = 5  # concurrent modification detected (incl. workspace lock)
EXIT_PARTIAL = 6  # partial operation; recovery required
EXIT_UNSAFE_BLOCKED = 7  # unsafe operation blocked; explicit flag missing
EXIT_OUTDATED = 8  # tool version outdated (update-check only)
EXIT_INTERNAL = 9  # internal or unexpected failure

# --- Machine-readable result codes (subset used across commands) -----------

CODE_OK = "OK"
CODE_DIFF_FOUND = "DIFF_FOUND"
CODE_PENDING_ACTIONS = "PENDING_ACTIONS"
CODE_CONFLICTS_PRESENT = "CONFLICTS_PRESENT"
CODE_INVALID_WORKSPACE = "INVALID_WORKSPACE"
CODE_INVALID_LOCAL_FILE = "INVALID_LOCAL_FILE"
CODE_API_UNAVAILABLE = "API_UNAVAILABLE"
CODE_API_AUTH_FAILED = "API_AUTH_FAILED"
CODE_CONCURRENT_MODIFICATION = "CONCURRENT_MODIFICATION"
CODE_WORKSPACE_LOCKED = "WORKSPACE_LOCKED"
CODE_RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
CODE_PARTIAL_FAILURE = "PARTIAL_FAILURE"
CODE_UNSAFE_BLOCKED = "UNSAFE_OPERATION_BLOCKED"
CODE_OUTDATED = "VERSION_OUTDATED"
CODE_UPDATE_CHECK_FAILED = "UPDATE_CHECK_FAILED"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"


class JoplinSyncError(Exception):
    """Base class for all expected tool failures.

    Every subclass carries a stable exit code and machine-readable result
    code so the CLI can map failures deterministically.
    """

    exit_code: int = EXIT_INTERNAL
    code: str = CODE_INTERNAL_ERROR

    def __init__(self, message: str, *, code: str | None = None, details: object = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.details = details


class WorkspaceError(JoplinSyncError):
    """The workspace is missing, malformed, or contains invalid managed files."""

    exit_code = EXIT_INVALID_WORKSPACE
    code = CODE_INVALID_WORKSPACE


class InvalidManagedFileError(WorkspaceError):
    """A managed Markdown file has a malformed metadata header."""

    code = CODE_INVALID_LOCAL_FILE


class ApiError(JoplinSyncError):
    """The Joplin API is unreachable or returned an unexpected error."""

    exit_code = EXIT_API
    code = CODE_API_UNAVAILABLE

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        details: object = None,
    ) -> None:
        super().__init__(message, code=code, details=details)
        self.status = status


class AuthError(ApiError):
    """The Joplin API rejected the token."""

    code = CODE_API_AUTH_FAILED


class ConcurrentModificationError(JoplinSyncError):
    """A note changed between planning and applying an operation."""

    exit_code = EXIT_CONCURRENT
    code = CODE_CONCURRENT_MODIFICATION


class WorkspaceLockedError(JoplinSyncError):
    """Another process holds the exclusive workspace lock."""

    exit_code = EXIT_CONCURRENT
    code = CODE_WORKSPACE_LOCKED


class RecoveryRequiredError(JoplinSyncError):
    """An incomplete journal from a previous run must be recovered first."""

    exit_code = EXIT_PARTIAL
    code = CODE_RECOVERY_REQUIRED


class PartialFailureError(JoplinSyncError):
    """Some operations of a plan failed; the journal records the details."""

    exit_code = EXIT_PARTIAL
    code = CODE_PARTIAL_FAILURE


class UnsafeOperationError(JoplinSyncError):
    """A destructive action was requested without its explicit flag."""

    exit_code = EXIT_UNSAFE_BLOCKED
    code = CODE_UNSAFE_BLOCKED


class InternalError(JoplinSyncError):
    """Unexpected internal failure."""

    exit_code = EXIT_INTERNAL
    code = CODE_INTERNAL_ERROR
