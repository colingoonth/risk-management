#!/usr/bin/env python3
"""Reject staged private data and sensitive commit messages.

This module intentionally uses only the Python standard library so Git can run
the hooks without activating the project's virtual environment. It never reads
the GroupMe token. Roster names come from a read-only connection to the local
application database and live in memory only for the duration of the hook.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse
from collections.abc import Sequence
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"

# GroupMe group, user, membership, and message IDs are long bare digit runs.
ID_SHAPED = re.compile(r"(?<![\w])\d{8,}(?![\w])")

# Catch secrets assigned to an obvious credential field even when their exact
# alphabet or future length changes. The second expression catches an unlabeled
# high-entropy-looking value in the range used by common access tokens.
LABELED_TOKEN = re.compile(
    r"(?ix)\b(?:"
    r"x[\s_-]*access[\s_-]*token|"
    r"groupme[\s_-]*(?:access[\s_-]*)?token|"
    r"api[\s_-]*token|"
    r"access[\s_-]*token"
    r")\b"
    r"\s*(?:=|:|is)\s*[\"']?[A-Za-z0-9._~+/=-]{8,}"
)
OPAQUE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?=[A-Za-z0-9]{32,64}(?![A-Za-z0-9]))"
    r"(?=[A-Za-z0-9]*[A-Za-z])"
    r"(?=[A-Za-z0-9]*\d)"
    r"[A-Za-z0-9]+"
)


class GuardError(RuntimeError):
    """The hook could not safely inspect the requested repository state."""


def _run_git(*args: str, text: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=text,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise GuardError(f"git {' '.join(args)} failed: {stderr}")
    return result.stdout


def _staged_paths() -> list[str]:
    output = _run_git(
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=ACMR",
        "-z",
    )
    assert isinstance(output, bytes)
    return [part.decode("utf-8", errors="surrogateescape") for part in output.split(b"\x00") if part]


def _staged_blob(path: str) -> bytes:
    output = _run_git("show", f":{path}")
    assert isinstance(output, bytes)
    return output


def _added_text(path: str) -> str:
    output = _run_git(
        "diff",
        "--cached",
        "--no-ext-diff",
        "--no-color",
        "--unified=0",
        "--diff-filter=ACMR",
        "--",
        path,
        text=True,
    )
    assert isinstance(output, str)
    return "\n".join(
        line[1:] for line in output.splitlines() if line.startswith("+") and not line.startswith("+++")
    )


def _default_db_path() -> Path:
    configured = os.environ.get("RISK_DB_PATH")
    if configured:
        return Path(configured)

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "risk" / "risk.db"

    if sys.platform == "darwin":
        default = (
            Path.home() / "Library" / "Application Support" / "risk-management" / "risk.db"
        )
    else:
        default = Path.home() / ".local" / "share" / "risk" / "risk.db"

    legacy = Path("data/risk.db")
    if legacy.exists() and not default.exists():
        return legacy
    return default


def _roster_names() -> tuple[str, ...]:
    """Read display names from the live local DB without creating or changing it."""

    db_path = _default_db_path().expanduser().resolve(strict=False)
    if not db_path.is_file():
        return ()

    quoted = urllib.parse.quote(str(db_path), safe="/")
    try:
        conn = sqlite3.connect(f"file:{quoted}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only = ON")
            has_members = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'members'"
            ).fetchone()
            if has_members is None:
                return ()
            rows = conn.execute("SELECT display_name FROM members").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise GuardError(f"could not inspect the local roster database at {db_path}: {exc}") from exc

    return tuple(name.strip() for (name,) in rows if isinstance(name, str) and name.strip())


def _contains_roster_name(text: str, names: Sequence[str]) -> bool:
    for name in names:
        pieces = [re.escape(piece) for piece in name.split()]
        pattern = rf"(?<!\w){r'\s+'.join(pieces)}(?!\w)"
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


def _text_findings(text: str, roster_names: Sequence[str]) -> list[str]:
    findings: list[str] = []
    if ID_SHAPED.search(text):
        findings.append("an ID-shaped digit run")
    if LABELED_TOKEN.search(text) or OPAQUE_TOKEN.search(text):
        findings.append("a token-shaped value")
    if _contains_roster_name(text, roster_names):
        findings.append("a name from the local roster")
    return findings


def check_staged() -> list[str]:
    names = _roster_names()
    findings: list[str] = []
    for path in _staged_paths():
        blob = _staged_blob(path)
        if blob.startswith(SQLITE_MAGIC):
            findings.append(f"{path}: SQLite database magic bytes")

        text = f"{path}\n{_added_text(path)}"
        findings.extend(f"{path}: {finding}" for finding in _text_findings(text, names))
    return findings


def check_commit_message(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GuardError(f"could not read commit message {path}: {exc}") from exc
    # Git removes comment lines before recording the commit, so do not treat its
    # commented status/template text as part of the message.
    message = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
    return [f"commit message: {finding}" for finding in _text_findings(message, _roster_names())]


def _print_rejection(findings: Sequence[str]) -> None:
    print("privacy guard: commit blocked", file=sys.stderr)
    for finding in findings:
        print(f"  - {finding}", file=sys.stderr)
    print(
        "Move private data outside the repository and replace credentials/IDs with placeholders.",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args == ["staged"]:
            findings = check_staged()
        elif len(args) == 2 and args[0] == "commit-msg":
            findings = check_commit_message(Path(args[1]))
        else:
            print("usage: privacy_guard.py staged | commit-msg MESSAGE_FILE", file=sys.stderr)
            return 2
    except GuardError as exc:
        print(f"privacy guard: unable to complete safety checks: {exc}", file=sys.stderr)
        return 2

    if findings:
        _print_rejection(findings)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
