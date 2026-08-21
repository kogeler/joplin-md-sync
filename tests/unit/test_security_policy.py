"""Static security invariants for the dependency-free runtime package."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
RUNTIME = ROOT / "src" / "joplin_md_sync"
FORBIDDEN_MODULES = {"commands", "pty", "subprocess"}
FORBIDDEN_BUILTINS = {"eval", "exec"}


def test_runtime_has_no_code_execution_surface() -> None:
    violations: list[str] = []
    for path in sorted(RUNTIME.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in FORBIDDEN_MODULES:
                        violations.append(f"{path.name}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".", 1)[0] in FORBIDDEN_MODULES:
                    violations.append(f"{path.name}:{node.lineno}: from {node.module}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_BUILTINS:
                    violations.append(f"{path.name}:{node.lineno}: {node.func.id}()")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr in {"popen", "system"}
                ):
                    violations.append(f"{path.name}:{node.lineno}: os.{node.func.attr}()")
    assert violations == []
