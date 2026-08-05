"""Load an events CSV into a semester. Idempotent; supports --dry-run.

    uv run python scripts/load_events.py events.csv --semester FA26 --dry-run
    uv run python scripts/load_events.py events.csv --semester FA26

Expected columns: date, weekday, display_name, event_type, host_house, status, note
See scripts/README.md for what each column means.

TWO THINGS THIS SCRIPT DECIDES, both worth knowing about:

1. DUPLICATE NAMES. ``events`` carries UNIQUE (semester_id, display_name), but a
   real schedule repeats names — "SK Mixer" happens four times a semester. A
   straight load inserts the first of each and throws on the rest.

   The constraint is not a mistake to route around: ``events_repo.resolve()``
   looks events up BY display_name, which is what makes
   ``risk event auto-assign "Halloween Krush @ Arena"`` work. Dropping
   uniqueness would make that lookup ambiguous, and rebuilding a STRICT table to
   change a constraint is destructive.

   So the DATA is disambiguated instead of the schema: a name appearing more
   than once gets a " (YYYY-MM-DD)" suffix. Already-unique names are untouched.

2. STATUS. ``events.status`` only permits created/assigned/completed/cancelled,
   so every row loads as the default 'created'. A planning status in the CSV
   (confirmed / potential / placeholder / ...) is real information — it is the
   difference between a party that is happening and a slot being held — so it is
   preserved in ``events.notes`` behind a greppable prefix, joined to the row's
   own note where there is one:

       status=placeholder; Thanksgiving week

   Filter with:  SELECT * FROM events WHERE notes LIKE 'status=placeholder%'
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import semesters as semesters_repo
from risk.services import shift_requirements as shift_req_svc


def build_notes(status: str, note: str) -> str | None:
    status, note = status.strip(), note.strip()
    if status and note:
        return f"status={status}; {note}"
    if status:
        return f"status={status}"
    return note or None


class _DryRunRollbackError(Exception):
    """Abort the transaction on --dry-run so nothing is written."""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv_path", type=Path, help="Events CSV to load.")
    ap.add_argument("--db", type=Path, required=True, help="Target database path.")
    ap.add_argument("--semester", required=True, help="Target semester name, e.g. FA26.")
    ap.add_argument(
        "--house",
        action="append",
        default=[],
        metavar="SLUG=NAME",
        help="House to create if absent, e.g. --house arena=Arena. Repeatable.",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.csv_path.exists():
        print(f"ERROR: {args.csv_path} does not exist", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(args.csv_path.open(newline="", encoding="utf-8-sig")))
    conn = connect(args.db)
    ensure_schema(conn)

    sem = semesters_repo.get_by_name(conn, args.semester)
    if sem is None:
        print(f"ERROR: semester {args.semester!r} not found", file=sys.stderr)
        return 1

    # Disambiguate repeated display names before touching the database.
    name_counts = Counter(r["display_name"].strip() for r in rows)
    planned = [
        {
            "name": (
                f"{r['display_name'].strip()} ({r['date'].strip()})"
                if name_counts[r["display_name"].strip()] > 1
                else r["display_name"].strip()
            ),
            "date": r["date"].strip(),
            "type": r["event_type"].strip(),
            "host": r["host_house"].strip() or None,
            "notes": build_notes(r.get("status", ""), r.get("note", "")),
        }
        for r in rows
    ]
    dup = [n for n, c in Counter(p["name"] for p in planned).items() if c > 1]
    if dup:
        print(f"ERROR: names still collide after disambiguation: {dup}", file=sys.stderr)
        return 1

    houses_made = events_made = events_skipped = 0
    with transaction(conn):
        for spec in args.house:
            slug, _, display = spec.partition("=")
            if not display:
                print(f"ERROR: --house wants SLUG=NAME, got {spec!r}", file=sys.stderr)
                return 1
            if houses_repo.get_by_slug(conn, slug) is None:
                houses_made += 1
                if not args.dry_run:
                    houses_repo.insert(conn, slug=slug, display_name=display)

        for p in planned:
            et = etypes_repo.get_by_slug(conn, str(p["type"]))
            if et is None:
                print(f"ERROR: no event type {p['type']!r}", file=sys.stderr)
                return 1
            host_id = None
            if p["host"] is not None:
                h = houses_repo.get_by_slug(conn, str(p["host"]))
                # On a dry run the houses above were not really inserted, so a
                # missing host is expected rather than fatal.
                if h is None and not args.dry_run:
                    print(f"ERROR: no house {p['host']!r}", file=sys.stderr)
                    return 1
                host_id = h.id if h else None

            if events_repo.get_by_semester_and_name(
                conn, semester_id=sem.id, display_name=str(p["name"])
            ):
                events_skipped += 1
                continue
            events_made += 1
            if not args.dry_run:
                event_id = events_repo.insert(
                    conn,
                    semester_id=sem.id,
                    event_type_id=et.id,
                    display_name=str(p["name"]),
                    date=str(p["date"]),
                    host_house_id=host_id,
                    notes=p["notes"],
                )
                # Materialise event_shift_requirements from the 3-layer merge
                # (per-event override > per-house preference > event-type default).
                shift_req_svc.snapshot_for_event(conn, event_id)

        verb = "would create" if args.dry_run else "created"
        renamed = sum(
            1
            for p, r in zip(planned, rows, strict=True)
            if p["name"] != r["display_name"].strip()
        )
        print(f"houses {verb}: {houses_made}")
        print(f"events {verb}: {events_made}   already present: {events_skipped}")
        print(f"names disambiguated with a date suffix: {renamed}")
        if args.dry_run:
            raise _DryRunRollbackError()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except _DryRunRollbackError:
        print("dry run — rolled back, nothing written")
        raise SystemExit(0) from None
