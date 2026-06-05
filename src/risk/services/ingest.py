"""Tier-2 ingest pipeline (Phase 7).

Tier-1 (Claude in the terminal) parses raw artifacts (CSV, PDF, paste) into
canonical JSON. Tier-2 (this module) validates the JSON, computes a diff
against the DB, and applies the diff as an idempotent upsert.

Strike sheet schema (Tier-1 emits this):
{
  "ingest_type": "strikes",
  "semester": "SP26",
  "entries": [
    {
      "member_slug": "alice",
      "issued_on": "2026-02-10",
      "reason": "no-show",
      "strike_category": "social_risk",   # optional, must match strike_categories slug
      "_parse_confidence": "high"          # optional
    },
    ...
  ]
}

Idempotency key: (member_id, semester_id, issued_on, reason). Re-running the
same file produces zero new strike rows.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services import strike_state


@dataclass(frozen=True, slots=True)
class IngestPreview:
    semester_name: str
    semester_id: int
    new_strikes: int
    no_op_strikes: int
    member_not_found: tuple[str, ...]
    low_confidence_entries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IngestResult:
    preview: IngestPreview
    applied_strike_ids: tuple[int, ...] = field(default=())


def load_strike_sheet(path: Path) -> dict[str, object]:
    """Read and minimally validate a Tier-1 JSON file."""
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("ingest JSON must be an object")
    if payload.get("ingest_type") != "strikes":
        raise ValueError(
            f"ingest_type must be 'strikes' (got {payload.get('ingest_type')!r})"
        )
    if not isinstance(payload.get("entries"), list):
        raise ValueError("ingest JSON missing 'entries' list")
    return payload


def preview_strike_sheet(
    conn: sqlite3.Connection,
    *,
    payload: dict[str, object],
    semester_name_override: str | None = None,
) -> IngestPreview:
    """Compute the diff without applying."""
    sem_name = semester_name_override or payload.get("semester")
    if not isinstance(sem_name, str) or not sem_name:
        raise ValueError(
            "no --semester given on CLI and payload missing top-level 'semester'"
        )
    sem = semesters_repo.get_by_name(conn, sem_name)
    if sem is None:
        raise LookupError(f"semester {sem_name!r} not found")
    if sem.archived_at is not None:
        raise ValueError(
            f"semester {sem_name!r} is archived ({sem.archived_at}); cannot ingest"
        )

    new_count = 0
    no_op_count = 0
    member_misses: list[str] = []
    low_conf: list[str] = []
    entries = payload["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"entry must be an object: {entry!r}")
        slug = entry.get("member_slug")
        issued_on = entry.get("issued_on")
        reason = entry.get("reason")
        if (
            not isinstance(slug, str)
            or not isinstance(issued_on, str)
            or not isinstance(reason, str)
        ):
            raise ValueError(
                f"entry missing required fields (member_slug, issued_on, reason): {entry!r}"
            )
        if entry.get("_parse_confidence") == "low":
            low_conf.append(slug)
        m = members_repo.resolve(conn, slug)
        if m is None:
            member_misses.append(slug)
            continue
        existing = conn.execute(
            """
            SELECT 1 FROM strikes
            WHERE member_id = ? AND semester_id = ?
              AND issued_on = ? AND reason = ?
            LIMIT 1
            """,
            (m.id, sem.id, issued_on, reason),
        ).fetchone()
        if existing is None:
            new_count += 1
        else:
            no_op_count += 1

    return IngestPreview(
        semester_name=sem.name,
        semester_id=sem.id,
        new_strikes=new_count,
        no_op_strikes=no_op_count,
        member_not_found=tuple(member_misses),
        low_confidence_entries=tuple(low_conf),
    )


def apply_strike_sheet(
    conn: sqlite3.Connection,
    *,
    payload: dict[str, object],
    semester_name_override: str | None = None,
) -> IngestResult:
    """Apply the diff. Idempotent — entries already in the DB are skipped."""
    preview = preview_strike_sheet(
        conn, payload=payload, semester_name_override=semester_name_override
    )
    inserted: list[int] = []
    entries = payload["entries"]
    assert isinstance(entries, list)
    # Sort by issued_on so monotonicity trigger is satisfied within the apply.
    entries_sorted = sorted(
        (e for e in entries if isinstance(e, dict)),
        key=lambda e: (str(e.get("issued_on", "")), str(e.get("member_slug", ""))),
    )
    for entry in entries_sorted:
        slug = entry["member_slug"]
        issued_on = entry["issued_on"]
        reason = entry["reason"]
        m = members_repo.resolve(conn, slug)
        if m is None:
            continue
        existing = conn.execute(
            """
            SELECT 1 FROM strikes
            WHERE member_id = ? AND semester_id = ?
              AND issued_on = ? AND reason = ?
            LIMIT 1
            """,
            (m.id, preview.semester_id, issued_on, reason),
        ).fetchone()
        if existing is not None:
            continue
        result = strike_state.issue_strike(
            conn,
            member_id=m.id,
            semester_id=preview.semester_id,
            issued_on=issued_on,
            reason=reason,
        )
        inserted.append(result.strike_id)
    return IngestResult(preview=preview, applied_strike_ids=tuple(inserted))
