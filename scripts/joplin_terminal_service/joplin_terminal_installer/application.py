"""Joplin Terminal installer: application."""

from __future__ import annotations

import logging
import os
import sys

from .cli import (
    build_parser,
)
from .common import (
    SecretRedactor,
    ToolError,
)
from .lifecycle import Installer

LOG = logging.getLogger("joplin-terminal-installer")


def configure_logging(verbose: bool, redactor: SecretRedactor) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(redactor)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    env = dict(os.environ)
    redactor = SecretRedactor()
    try:
        args = build_parser(env).parse_args(argv)
        redactor.add(env.get("JOPLIN_MCP_AUTH_TOKEN"))
        redactor.add(env.get("JOPLIN_GPT_ACTIONS_TOKEN"))
        configure_logging(args.verbose, redactor)
        Installer(args, env=env, redactor=redactor).run()
        return 0
    except ToolError as exc:
        if not logging.getLogger().handlers:
            configure_logging(False, redactor)
        LOG.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOG.error("installation interrupted")
        return 130
    except Exception as exc:
        if not logging.getLogger().handlers:
            configure_logging(False, redactor)
        verbose = "args" in locals() and bool(args.verbose)
        if verbose:
            LOG.exception("unexpected installer failure")
        else:
            LOG.error("unexpected installer failure: %s", type(exc).__name__)
        return 1
    finally:
        redactor.clear()
