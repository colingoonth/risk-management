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

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from risk.repos import member_roles as member_roles_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import roles as roles_repo
from risk.repos import semesters as semesters_repo
from risk.services import policy, strike_state


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


# =========================================================================
# Google Form roster intake (Phase 9 / frontend).
#
# The roster Google Form has a FIXED schema (Full Name, Rising Class, PC,
# EC), so unlike the strike sheet there is no Tier-1 LLM parse step — the CSV
# export is read directly with the stdlib (ADR-014: zero API on any path).
#
# Idempotency key: member slug derived from full name. Re-running the same
# export inserts zero new members; the exec role is re-applied idempotently.
# =========================================================================

EXEC_ROLE_SLUG = "exec"

# "Rising X" → years until graduation, where X refers to the UPCOMING academic
# year. Graduation year = the semester's start calendar year + this offset.
# (Rising senior in SP26/FA26 → graduates spring 2027 = 2026 + 1.)
_RISING_CLASS_OFFSET: dict[str, int] = {
    "senior": 1,
    "junior": 2,
    "sophomore": 3,
    "freshman": 4,
    "first-year": 4,
    "first year": 4,
    "freshmen": 4,
}

_EXEC_TRUTHY: frozenset[str] = frozenset({"yes", "y", "true", "1", "x", "exec", "ec"})


@dataclass(frozen=True, slots=True)
class RosterRow:
    line: int  # 1-based data line (excludes header) for diagnostics
    full_name: str
    slug: str
    class_year: int | None
    pledge_class: str | None
    is_exec: bool


@dataclass(frozen=True, slots=True)
class GformPreview:
    semester_name: str
    semester_id: int
    base_year: int
    rows: tuple[RosterRow, ...]
    new_members: tuple[str, ...]
    existing_members: tuple[str, ...]
    exec_assignments: tuple[str, ...]
    unmapped_rising_class: tuple[str, ...]
    unmapped_pledge_class: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GformResult:
    preview: GformPreview
    inserted_member_ids: tuple[int, ...] = field(default=())
    exec_roles_set: int = 0


def _slugify(name: str) -> str:
    """Derive a schema-valid member slug (``[a-z][a-z0-9_-]*``) from a name."""
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not s:
        return "member"
    if not s[0].isalpha():
        s = f"m-{s}"
    return s


def _unique_slug(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _class_year_from_rising(rising: str | None, base_year: int) -> int | None:
    if not rising or not rising.strip():
        return None
    key = rising.strip().lower()
    if key.startswith("rising"):
        key = key[len("rising") :].strip()
    offset = _RISING_CLASS_OFFSET.get(key)
    return base_year + offset if offset is not None else None


def _normalize_pledge_class(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    return value.strip().title()


def _is_exec(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _EXEC_TRUTHY


def _find_column(fieldnames: list[str], *keywords: str, exclude: tuple[str, ...] = ()) -> str | None:
    """First header whose lowercased name contains any keyword (and no exclude term)."""
    for fn in fieldnames:
        low = fn.lower()
        if any(ex in low for ex in exclude):
            continue
        if any(kw in low for kw in keywords):
            return fn
    return None


def load_gform_roster(path: Path, *, base_year: int) -> list[RosterRow]:
    """Parse a Google-Form roster CSV export into validated ``RosterRow``s.

    Headers are matched case-insensitively by substring so the verbose phrasing
    Google Forms emits ("What is your full name?") still resolves. ``base_year``
    is the target semester's start year, used to map rising-class → class_year.
    """
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            raise ValueError("roster CSV has no header row")
        name_col = _find_column(fieldnames, "name")
        if name_col is None:
            raise ValueError(
                f"roster CSV has no recognizable name column (headers: {fieldnames})"
            )
        rising_col = _find_column(
            fieldnames, "rising", "year", "class", exclude=("pledge", "pc")
        )
        pc_col = _find_column(fieldnames, "pledge") or _find_column(fieldnames, "pc")
        ec_col = _find_column(fieldnames, "exec", "board") or _find_column(fieldnames, "ec")

        rows: list[RosterRow] = []
        taken: set[str] = set()
        for i, raw in enumerate(reader, start=1):
            full_name = (raw.get(name_col) or "").strip()
            if not full_name:
                continue  # skip blank rows (trailing newline, empty submissions)
            slug = _unique_slug(_slugify(full_name), taken)
            taken.add(slug)
            rows.append(
                RosterRow(
                    line=i,
                    full_name=full_name,
                    slug=slug,
                    class_year=_class_year_from_rising(
                        raw.get(rising_col) if rising_col else None, base_year
                    ),
                    pledge_class=_normalize_pledge_class(raw.get(pc_col) if pc_col else None),
                    is_exec=_is_exec(raw.get(ec_col) if ec_col else None),
                )
            )
    return rows


def _resolve_semester(conn: sqlite3.Connection, name: str) -> semesters_repo.Semester:
    sem = semesters_repo.get_by_name(conn, name)
    if sem is None:
        raise LookupError(f"semester {name!r} not found")
    if sem.archived_at is not None:
        raise ValueError(f"semester {name!r} is archived ({sem.archived_at}); cannot ingest")
    return sem


def preview_gform_roster(
    conn: sqlite3.Connection, *, path: Path, semester_name: str
) -> GformPreview:
    """Compute the roster diff without applying."""
    sem = _resolve_semester(conn, semester_name)
    base_year = int(sem.starts_on[:4])
    rows = load_gform_roster(path, base_year=base_year)

    new_members: list[str] = []
    existing: list[str] = []
    exec_assignments: list[str] = []
    unmapped_rising: list[str] = []
    unmapped_pc: list[str] = []
    for row in rows:
        if members_repo.get_by_slug(conn, row.slug) is None:
            new_members.append(row.slug)
        else:
            existing.append(row.slug)
        if row.is_exec:
            exec_assignments.append(row.slug)
        if row.class_year is None:
            unmapped_rising.append(row.full_name)
        if row.pledge_class is not None and policy.pledge_class_ordinal(row.pledge_class) is None:
            unmapped_pc.append(row.full_name)

    return GformPreview(
        semester_name=sem.name,
        semester_id=sem.id,
        base_year=base_year,
        rows=tuple(rows),
        new_members=tuple(new_members),
        existing_members=tuple(existing),
        exec_assignments=tuple(exec_assignments),
        unmapped_rising_class=tuple(unmapped_rising),
        unmapped_pledge_class=tuple(unmapped_pc),
    )


def apply_gform_roster(
    conn: sqlite3.Connection, *, path: Path, semester_name: str
) -> GformResult:
    """Apply the roster diff. Idempotent — existing slugs are skipped."""
    preview = preview_gform_roster(conn, path=path, semester_name=semester_name)
    active = statuses_repo.get_by_slug(conn, "active")
    if active is None:
        raise LookupError("member status 'active' not found — schema not seeded")
    exec_role = roles_repo.get_by_slug(conn, EXEC_ROLE_SLUG)

    inserted: list[int] = []
    exec_set = 0
    for row in preview.rows:
        member = members_repo.get_by_slug(conn, row.slug)
        if member is None:
            member_id = members_repo.insert(
                conn,
                slug=row.slug,
                display_name=row.full_name,
                status_id=active.id,
                class_year=row.class_year,
                pledge_class=row.pledge_class,
            )
            inserted.append(member_id)
        else:
            member_id = member.id
        if row.is_exec and exec_role is not None:
            exec_set += member_roles_repo.set_role(
                conn,
                member_id=member_id,
                role_id=exec_role.id,
                semester_id=preview.semester_id,
            )
    return GformResult(
        preview=preview,
        inserted_member_ids=tuple(inserted),
        exec_roles_set=exec_set,
    )
