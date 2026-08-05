"""Apply a roster sidecar JSON to the database. Idempotent; supports --dry-run.

    uv run python scripts/load_sidecar.py sidecar.json --db risk.db --semester FA26 --dry-run
    uv run python scripts/load_sidecar.py sidecar.json --db risk.db --semester FA26

Run AFTER ``risk ingest gform-roster`` — every key in the sidecar has to resolve
to a member that already exists.

The roster CSV carries only what the Google Form asks for. Everything that has to
be reconciled by hand goes in the sidecar, as four maps keyed by full name:

    aliases        {"Full Name": ["Other Spelling", ...]}
    roles          {"full name": "automation-key"}          e.g. "risk-chair"
    notes          {"full name": "why they are exempt"}
    qualifications {"Full Name": ["dj", ...]}

This is a one-time data load, so it is a script. The ONGOING operations have real
CLI commands, because they keep happening after the load:

    risk member qualify   <member> <qual>    # the DJ job changes hands
    risk member unqualify <member> <qual>
    risk member set-notes <member> "<text>"  # an exemption reason changes
    risk member set-role  <member> <role>
    risk member add-alias <member> <alias>   # a new spelling shows up
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import member_qualifications as mq_repo
from risk.repos import member_roles as mr_repo
from risk.repos import members as members_repo
from risk.repos import qualifications as quals_repo
from risk.repos import roles as roles_repo
from risk.repos import semesters as semesters_repo
from risk.services.ingest import _slugify

SECTIONS = ("aliases", "roles", "notes", "qualifications")


class _DryRunRollbackError(Exception):
    """Abort the transaction on --dry-run so nothing is written."""


class _LoadError(Exception):
    """Abort the transaction on a data error so nothing is half-written.

    Must be raised, never returned. ``transaction()`` rolls back on an exception
    and commits on any normal exit, so a bare ``return 1`` from inside the block
    reports failure while committing every row applied up to that point — which
    contradicted this script's own promise that a bad slug applies nothing.
    """


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sidecar_path", type=Path, help="Sidecar JSON to apply.")
    ap.add_argument("--db", type=Path, required=True, help="Target database path.")
    ap.add_argument("--semester", required=True, help="Target semester name, e.g. FA26.")
    ap.add_argument(
        "--alias-source",
        default="roster-reconciliation",
        help="Recorded in member_aliases.source for provenance.",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.sidecar_path.exists():
        print(f"ERROR: {args.sidecar_path} does not exist", file=sys.stderr)
        return 1

    side = json.loads(args.sidecar_path.read_text())
    conn = connect(args.db)
    ensure_schema(conn)

    sem = semesters_repo.get_by_name(conn, args.semester)
    if sem is None:
        print(f"ERROR: semester {args.semester!r} not found", file=sys.stderr)
        return 1

    def resolve(name: str) -> members_repo.Member:
        m = members_repo.get_by_slug(conn, _slugify(name))
        if m is None:
            m = members_repo.resolve(conn, name)
        if m is None:
            raise LookupError(f"cannot resolve {name!r} to a member")
        return m

    # Resolve every key BEFORE writing anything. A name that does not resolve
    # would silently drop that member's roles, notes or qualifications — and a
    # missing `dj` qualification means nobody can fill a DJ slot. A partial load
    # is worse than no load, because it looks like it worked.
    unresolved = []
    for section in SECTIONS:
        for name in side.get(section, {}):
            try:
                resolve(name)
            except LookupError:
                unresolved.append(f"{section}: {name}")
    if unresolved:
        print("ERROR: unresolved names, refusing to apply:", file=sys.stderr)
        for u in unresolved:
            print(f"  {u}", file=sys.stderr)
        print(
            "  (run `risk ingest gform-roster` first, or add the spelling "
            "with `risk member add-alias`)",
            file=sys.stderr,
        )
        return 1

    planned = dict.fromkeys(SECTIONS, 0)
    skipped = dict.fromkeys(SECTIONS, 0)

    with transaction(conn):
        for name, alts in side.get("aliases", {}).items():
            m = resolve(name)
            existing = {a.alias for a in members_repo.list_aliases(conn, m.id)}
            for alt in alts:
                if alt in existing:
                    skipped["aliases"] += 1
                    continue
                planned["aliases"] += 1
                if not args.dry_run:
                    members_repo.add_alias(
                        conn, member_id=m.id, alias=alt, source=args.alias_source
                    )

        # Sidecar values are automation KEYS ('risk-chair'); the roles table is
        # keyed by SLUG ('risk_chair'). Map through automation_key so the two
        # spellings cannot drift apart.
        by_key = {r.automation_key: r for r in roles_repo.list_all(conn) if r.automation_key}
        for name, role_key in side.get("roles", {}).items():
            m = resolve(name)
            role = by_key.get(role_key)
            if role is None:
                raise _LoadError(f"no role with automation_key {role_key!r}")
            held = {
                r.role_slug
                for r in mr_repo.list_for_member_in_semester(
                    conn, member_id=m.id, semester_id=sem.id
                )
            }
            if role.slug in held:
                skipped["roles"] += 1
                continue
            planned["roles"] += 1
            if not args.dry_run:
                mr_repo.set_role(conn, member_id=m.id, role_id=role.id, semester_id=sem.id)

        for name, note in side.get("notes", {}).items():
            m = resolve(name)
            if m.notes == note:
                skipped["notes"] += 1
                continue
            planned["notes"] += 1
            if not args.dry_run:
                members_repo.update_notes(conn, member_id=m.id, notes=note)

        by_slug = {q.slug: q for q in quals_repo.list_all(conn)}
        for name, quals in side.get("qualifications", {}).items():
            m = resolve(name)
            held = {
                r.qualification_slug
                for r in mq_repo.list_for_member_in_semester(
                    conn, member_id=m.id, semester_id=sem.id
                )
            }
            for qslug in quals:
                q = by_slug.get(qslug)
                if q is None:
                    raise _LoadError(f"no qualification {qslug!r}")
                if qslug in held:
                    skipped["qualifications"] += 1
                    continue
                planned["qualifications"] += 1
                if not args.dry_run:
                    mq_repo.grant(
                        conn, member_id=m.id, qualification_id=q.id, semester_id=sem.id
                    )

        verb = "would apply" if args.dry_run else "applied"
        print(f"{'section':16}{verb:>14}{'already present':>18}")
        for k in SECTIONS:
            print(f"{k:16}{planned[k]:>14}{skipped[k]:>18}")
        if args.dry_run:
            raise _DryRunRollbackError()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except _DryRunRollbackError:
        print("dry run — rolled back, nothing written")
        raise SystemExit(0) from None
    except _LoadError as exc:
        print(f"ERROR: {exc} — rolled back, nothing written", file=sys.stderr)
        raise SystemExit(1) from None
