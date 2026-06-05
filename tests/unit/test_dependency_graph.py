"""AST + import-linter dependency-graph enforcement.

ADR-003: ``services.policy`` must be pure (no DB or repo imports). This test
is defense-in-depth alongside ``.importlinter`` so the property holds even if
the import-linter run is skipped.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

POLICY_PATH = Path(__file__).resolve().parents[2] / "src" / "risk" / "services" / "policy.py"
REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_PREFIXES = ("risk.repos", "risk.db", "sqlite3")


def _collect_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_policy_has_no_db_or_repo_imports() -> None:
    source = POLICY_PATH.read_text()
    imports = _collect_imports(source)
    bad = {name for name in imports if name.startswith(FORBIDDEN_PREFIXES)}
    assert not bad, f"services.policy imports forbidden modules: {sorted(bad)}"


def test_importlinter_contracts_pass() -> None:
    """Run ``lint-imports`` and assert exit 0.

    Catches violations beyond the policy purity rule — the full set of
    contracts defined in ``.importlinter``.
    """
    cli = shutil.which("lint-imports")
    if cli is None:
        pytest.skip("lint-imports not on PATH")
    result = subprocess.run(
        [cli],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"import-linter failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
