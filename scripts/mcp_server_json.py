#!/usr/bin/env python3
"""Write the official MCP Registry server.json for the current release.

Package name, links and version come from pyproject.toml and .version. The
registry proves PyPI ownership through an `mcp-name:` line in the README, which
becomes the PyPI project description, so the name must appear there too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME = "io.github.kogeler/joplin-md-sync"
SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
TITLE = "Joplin MCP for Claude & ChatGPT"
DESCRIPTION = (
    "Self-hosted Joplin MCP server with built-in OAuth for hosted Claude and ChatGPT Actions"
)


def server(root: Path = REPO) -> dict[str, object]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = (root / ".version").read_text(encoding="utf-8").strip()
    readme = (root / project["readme"]).read_text(encoding="utf-8")
    if not re.search(rf"(?m)^(?:<!-- )?mcp-name: {re.escape(NAME)}(?: -->)?$", readme):
        raise ValueError(f"{project['readme']} must contain a line 'mcp-name: {NAME}'")
    return {
        "$schema": SCHEMA,
        "name": NAME,
        "title": TITLE,
        "description": DESCRIPTION,
        "version": version,
        "websiteUrl": project["urls"]["Homepage"],
        "repository": {"url": project["urls"]["Repository"], "source": "github"},
        "packages": [
            {
                "registryType": "pypi",
                "registryBaseUrl": "https://pypi.org",
                "identifier": project["name"],
                "version": version,
                "runtimeHint": "uvx",
                "transport": {"type": "stdio"},
                "packageArguments": [
                    {"type": "positional", "value": "mcp"},
                    {"type": "positional", "value": "stdio"},
                    {
                        "type": "named",
                        "name": "--port",
                        "valueHint": "port",
                        "description": "Joplin Web Clipper port when it is not the default",
                        "default": "41184",
                        "isRequired": False,
                    },
                ],
                "environmentVariables": [
                    {
                        "name": "JOPLIN_TOKEN",
                        "description": "Joplin Web Clipper authorization token",
                        "isRequired": True,
                        "isSecret": True,
                    }
                ],
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        document = server()
    except (OSError, KeyError, ValueError) as exc:
        print(f"server.json failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} for {NAME} {document['version']}")


if __name__ == "__main__":
    main()
