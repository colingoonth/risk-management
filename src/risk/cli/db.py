"""``risk db ...`` subcommands — backup + maintenance.

ADR R3.1-B (zero-cost runtime): no cloud backup tier; chair runs
``risk db backup PATH`` to take an online-consistent copy.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import emit_error, emit_success
from risk.db.connection import connect

app = typer.Typer(help="Database backup + maintenance.")


def _git_worktree_containing(path: Path) -> Path | None:
    """Return the worktree root containing ``path``, including uncreated paths.

    Both the lexical path and its resolved target are checked. The lexical pass
    prevents a destination such as ``repo/backup.db`` from escaping through an
    existing symlink; the resolved pass catches a path outside the repository
    that points back into it.
    """

    candidates = {path.absolute(), path.resolve(strict=False)}
    for target in candidates:
        start = target if target.is_dir() else target.parent
        for directory in (start, *start.parents):
            if not directory.exists() or not directory.is_dir():
                continue
            try:
                result = subprocess.run(
                    ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except FileNotFoundError:
                return None
            if result.returncode != 0:
                continue
            root = Path(result.stdout.strip()).resolve()
            if target == root or root in target.parents:
                return root
    return None


@app.command("backup")
def backup(
    ctx: typer.Context,
    dest: Annotated[
        Path,
        typer.Argument(help="Destination path for the backup .db file. Parent dirs auto-created."),
    ],
) -> None:
    """Hot-copy the live DB to ``dest`` via SQLite's online-backup API.

    Safe to run while other processes are reading/writing — SQLite's backup
    API takes a consistent snapshot without blocking writers.
    """
    mode = mode_from_ctx(ctx)

    if dest.exists() and dest.is_dir():
        emit_error("backup.dest_is_dir", f"{dest} is a directory.", mode=mode)

    worktree = _git_worktree_containing(dest)
    if worktree is not None:
        emit_error(
            "backup.dest_in_git_worktree",
            f"Refusing to write a database backup inside Git worktree {worktree}.",
            mode=mode,
        )

    conn = open_conn(ctx)

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Write to a sibling temp file then atomic-rename, so a partial backup
    # never overwrites an existing good file and a stale dest gets replaced
    # cleanly (sqlite3.Connection.backup requires a clean target file).
    tmp = dest.with_name(dest.name + ".partial")
    tmp.unlink(missing_ok=True)
    dst_conn = connect(tmp)
    try:
        conn.backup(dst_conn)
    finally:
        dst_conn.close()
    tmp.replace(dest)

    size = dest.stat().st_size
    emit_success({"dest": str(dest), "bytes": size}, mode=mode)
