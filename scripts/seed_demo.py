"""Seed a demo DB for frontend smoke-testing. Not part of the test suite."""

from __future__ import annotations

import sys
from pathlib import Path

from risk.db.connection import close_conn, connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import semesters as semesters_repo
from risk.services import ingest as ingest_svc
from risk.services import shift_requirements as reqs_svc

ROSTER = """Full Name,Rising Class,PC,EC
Alex Rojas,Rising Senior,Zeta,Yes
Ben Carter,Rising Junior,Eta,No
Caleb Nguyen,Rising Sophomore,Theta,No
Dev Patel,Rising Senior,Zeta,Yes
Eli Brooks,Rising Junior,Eta,No
Finn O'Hara,Rising Sophomore,Theta,No
Gabe Moreno,Rising Freshman,Iota,No
Hank Lee,Rising Senior,Zeta,No
Ivan Cruz,Rising Junior,Eta,No
Jake Mills,Rising Sophomore,Theta,No
"""


def main(db_path: str) -> None:
    path = Path(db_path)
    path.unlink(missing_ok=True)
    conn = connect(path)
    ensure_schema(conn)
    with transaction(conn):
        sem_id = semesters_repo.insert(
            conn, name="FA26", starts_on="2026-08-20", ends_on="2026-12-15"
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
        for slug, name in [("zta", "Zeta Tau Alpha"), ("axid", "Alpha Xi Delta")]:
            houses_repo.insert(conn, slug=slug, display_name=name)

    roster = path.with_suffix(".roster.csv")
    roster.write_text(ROSTER, encoding="utf-8")
    with transaction(conn):
        ingest_svc.apply_gform_roster(conn, path=roster, semester_name="FA26")

    mixer = etypes_repo.get_by_slug(conn, "mixer")
    assert mixer is not None
    zta = houses_repo.get_by_slug(conn, "zta")
    assert zta is not None
    with transaction(conn):
        for name, date, host in [
            ("ZTA Mixer", "2026-09-12", zta.id),
            ("AXiD Mixer", "2026-09-19", None),
            ("Fall Philanthropy", "2026-09-26", None),
        ]:
            ev_id = events_repo.insert(
                conn,
                semester_id=sem_id,
                event_type_id=mixer.id,
                display_name=name,
                date=date,
                host_house_id=host,
            )
            reqs_svc.snapshot_for_event(conn, ev_id)
    close_conn(conn)
    print(f"Seeded demo DB at {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/risk-demo.db")
