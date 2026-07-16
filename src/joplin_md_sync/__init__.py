"""joplin-md-sync: safe two-way sync between Joplin and a local Markdown workspace.

The version's single source is the ``.version`` file at the repository
root (pyproject.toml reads it via setuptools dynamic metadata; the agent
manifest and CI verify against it). At runtime it is resolved from, in
order: a copy shipped inside the package (zipapp), the repository root
(source checkout), or the installed distribution metadata (wheel/sdist).
"""

from __future__ import annotations


def _resolve_version() -> str:
    # 1) Copy shipped next to the package (the zipapp build embeds one).
    try:
        from importlib import resources

        return resources.files(__name__).joinpath(".version").read_text(encoding="utf-8").strip()
    except Exception:  # fall through to the next source
        pass
    # 2) Source checkout: .version at the repository root (src layout).
    try:
        from pathlib import Path

        return (
            (Path(__file__).resolve().parents[2] / ".version").read_text(encoding="utf-8").strip()
        )
    except OSError:
        pass
    # 3) Installed wheel/sdist: distribution metadata.
    try:
        from importlib import metadata

        return metadata.version("joplin-md-sync")
    except Exception:
        return "0.0.0+unknown"


__version__ = _resolve_version()

#: Version of the Joplin Data API interaction contract implemented by this tool.
PROTOCOL_VERSION = 1

#: Version of the on-disk state database schema (.joplin-sync/state.sqlite3).
STATE_SCHEMA_VERSION = 1

#: Version of the machine-readable --json output envelope.
OUTPUT_SCHEMA_VERSION = 1

#: Version of the managed Markdown metadata header ("schema" key in the header).
NOTE_METADATA_SCHEMA_VERSION = 1

#: Public repository.
REPOSITORY_URL = "https://github.com/kogeler/joplin-md-sync"
