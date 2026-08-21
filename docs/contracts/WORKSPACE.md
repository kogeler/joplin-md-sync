<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Workspace Contract

## Assertions

### `WSP-001` - Managed note headers are strict and deterministic

**Contract:** A managed note header MUST be one first-line HTML comment
containing compact key-sorted JSON with schema `1`, optional 32-hex `id`,
`title`, and canonical `tags`, followed by exactly one blank line. Unknown
keys, invalid IDs, wrong schemas, multiline comments, or malformed separators
MUST invalidate the file rather than guess intent. An absent header MUST mean a
new local note.

**Evidence:**

- [`test_header_is_single_first_line_with_sorted_keys`](../../tests/unit/test_metadata.py) - `tests/unit/test_metadata.py::HeaderTest::test_header_is_single_first_line_with_sorted_keys`
- [`test_missing_id_means_new_note`](../../tests/unit/test_metadata.py) - `tests/unit/test_metadata.py::HeaderTest::test_missing_id_means_new_note`
- [`test_wrong_schema`](../../tests/unit/test_metadata.py) - `tests/unit/test_metadata.py::MalformedHeaderTest::test_wrong_schema`
- [`test_bad_id`](../../tests/unit/test_metadata.py) - `tests/unit/test_metadata.py::MalformedHeaderTest::test_bad_id`
- [`test_unknown_keys`](../../tests/unit/test_metadata.py) - `tests/unit/test_metadata.py::MalformedHeaderTest::test_unknown_keys`
- [`test_missing_blank_separator`](../../tests/unit/test_metadata.py) - `tests/unit/test_metadata.py::MalformedHeaderTest::test_missing_blank_separator`
- [`test_multiline_comment_rejected`](../../tests/unit/test_metadata.py) - `tests/unit/test_metadata.py::MalformedHeaderTest::test_multiline_comment_rejected`

### `WSP-002` - Canonicalization preserves user content

**Contract:** Body canonicalization MUST normalize CRLF and bare CR to LF and
MUST preserve Unicode, trailing whitespace, Markdown, and embedded Joplin
links. Tags MUST be lowercased, sorted, deduplicated, and stripped of empty
values. Hashing MUST be deterministic and isolate body, title, tags, and
parent components.

**Evidence:**

- [`test_trailing_whitespace_preserved`](../../tests/unit/test_canonical.py) - `tests/unit/test_canonical.py::CanonicalizeBodyTest::test_trailing_whitespace_preserved`
- [`test_unicode_not_normalized`](../../tests/unit/test_canonical.py) - `tests/unit/test_canonical.py::CanonicalizeBodyTest::test_unicode_not_normalized`
- [`test_bare_cr_normalized`](../../tests/unit/test_canonical.py) - `tests/unit/test_canonical.py::CanonicalizeBodyTest::test_bare_cr_normalized`
- [`test_lowercased_sorted_deduplicated`](../../tests/unit/test_canonical.py) - `tests/unit/test_canonical.py::CanonicalizeTagsTest::test_lowercased_sorted_deduplicated`
- [`test_empty_tags_dropped`](../../tests/unit/test_canonical.py) - `tests/unit/test_canonical.py::CanonicalizeTagsTest::test_empty_tags_dropped`
- [`test_deterministic`](../../tests/unit/test_canonical.py) - `tests/unit/test_canonical.py::NoteHashesTest::test_deterministic`
- [`test_component_isolation`](../../tests/unit/test_canonical.py) - `tests/unit/test_canonical.py::NoteHashesTest::test_component_isolation`
- [`test_crlf_body_canonicalized`](../../tests/integration/test_init_pull.py) - `tests/integration/test_init_pull.py::PullShapeTest::test_crlf_body_canonicalized`

### `WSP-003` - Generated paths are portable and confined

**Contract:** Note and notebook names derived from remote titles MUST be safe
on supported Windows and Linux filesystems, preserve usable Unicode, handle
reserved names and case-insensitive sibling collisions, and remain inside the
workspace. Filesystem scanning MUST NOT follow symlinks.

**Evidence:**

- [`test_windows_reserved_names`](../../tests/unit/test_paths.py) - `tests/unit/test_paths.py::SanitizeTest::test_windows_reserved_names`
- [`test_invalid_windows_chars_replaced`](../../tests/unit/test_paths.py) - `tests/unit/test_paths.py::SanitizeTest::test_invalid_windows_chars_replaced`
- [`test_trailing_dots_and_spaces`](../../tests/unit/test_paths.py) - `tests/unit/test_paths.py::SanitizeTest::test_trailing_dots_and_spaces`
- [`test_unicode_preserved`](../../tests/unit/test_paths.py) - `tests/unit/test_paths.py::SanitizeTest::test_unicode_preserved`
- [`test_long_titles_truncated`](../../tests/unit/test_paths.py) - `tests/unit/test_paths.py::SanitizeTest::test_long_titles_truncated`
- [`test_case_insensitive_folder_collision`](../../tests/unit/test_paths.py) - `tests/unit/test_paths.py::FilenameTest::test_case_insensitive_folder_collision`
- [`test_traversal_rejected`](../../tests/unit/test_paths.py) - `tests/unit/test_paths.py::SafeRelPathTest::test_traversal_rejected`
- [`test_symlink_not_followed`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::SecurityScanTest::test_symlink_not_followed`

### `WSP-004` - Identity is independent of cosmetic paths

**Contract:** Note identity MUST live in the header ID and notebook identity
MUST live in `.joplin-folder.json`; filenames and directory names are cosmetic.
Pull MUST normalize title-derived paths. Nested and empty notebooks MUST be
represented, and a new directory without metadata MAY become a notebook on
push.

**Evidence:**

- [`test_title_change_renames_file_and_updates_remote`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_title_change_renames_file_and_updates_remote`
- [`test_note_moved_between_notebooks`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_note_moved_between_notebooks`
- [`test_nested_and_empty_notebooks`](../../tests/integration/test_init_pull.py) - `tests/integration/test_init_pull.py::PullShapeTest::test_nested_and_empty_notebooks`
- [`test_new_local_notebook_pushed`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_new_local_notebook_pushed`

### `WSP-005` - Internal state is reserved, recoverable, and token-free

**Contract:** `.joplin-sync` MUST contain only tool-owned configuration,
SQLite state, journals, backups, quarantine, conflicts, and downloaded
resources and MUST be ignored by Git. The Joplin token MUST NOT be persisted
there. State schema migration MUST be ordered; corrupt or newer unsupported
state MUST fail explicitly.

**Evidence:**

- [`test_remote_first_init_and_pull`](../../tests/integration/test_init_pull.py) - `tests/integration/test_init_pull.py::InitTest::test_remote_first_init_and_pull`
- [`test_token_not_stored_in_workspace`](../../tests/integration/test_cli_contract.py) - `tests/integration/test_cli_contract.py::TokenSafetyTest::test_token_not_stored_in_workspace`
- [`test_corrupt_db_detected`](../../tests/unit/test_state.py) - `tests/unit/test_state.py::StateDBTest::test_corrupt_db_detected`
- [`test_newer_schema_rejected`](../../tests/unit/test_state.py) - `tests/unit/test_state.py::StateDBTest::test_newer_schema_rejected`
- [`test_migration_path_applied`](../../tests/unit/test_state.py) - `tests/unit/test_state.py::StateDBTest::test_migration_path_applied`

### `WSP-006` - New and reconstructed notes never match by title alone

**Contract:** A local file without an ID MUST create a new remote note and be
rewritten atomically with the assigned ID. A local file with an unknown ID MUST
not be treated as new. After state loss, equal identified sides MAY be adopted
as a base, while divergent sides MUST conflict. Title equality alone MUST NOT
adopt an unrelated remote note.

**Evidence:**

- [`test_new_headerless_note_adopted_on_push`](../../tests/integration/test_push_pull.py) - `tests/integration/test_push_pull.py::PushTest::test_new_headerless_note_adopted_on_push`
- [`test_local_with_unknown_id_is_invalid`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::StateMatrixTest::test_local_with_unknown_id_is_invalid`
- [`test_no_base_identical_adopts`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::StateMatrixTest::test_no_base_identical_adopts`
- [`test_no_base_divergent_is_conflict`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::StateMatrixTest::test_no_base_divergent_is_conflict`
- [`test_local_new_does_not_adopt_remote_note_with_same_title`](../../tests/unit/test_planner.py) - `tests/unit/test_planner.py::StateMatrixTest::test_local_new_does_not_adopt_remote_note_with_same_title`
