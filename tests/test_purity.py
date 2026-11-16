"""core/ must stay importable without any UI toolkit (CLI, MCP and headless use it)."""

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "src" / "monospace" / "core"
BANNED = ("textual", "textual_image", "rich", "monospace.ui", "monospace.render")


def test_core_has_no_ui_imports():
    offenders = []
    for path in CORE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] in BANNED or n.startswith(BANNED)]
    assert offenders == []


def test_no_file_over_350_lines():
    root = Path(__file__).resolve().parents[1]
    long = [str(p.relative_to(root)) for p in list((root / "src").rglob("*.py")) + list((root / "tests").rglob("*.py"))
            if len(p.read_text().splitlines()) > 350]
    assert long == []
