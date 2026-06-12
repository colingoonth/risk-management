"""Semester CRUD + pledge-mode (pledge-takeover workflow)."""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import (
    ArchiveIn,
    ArchiveReportOut,
    ArchiveResultOut,
    HouseModeIn,
    HouseModeOut,
    SemesterIn,
    SemesterOut,
)
from risk.db.connection import transaction
from risk.repos import house_semester_status as hss_repo
from risk.repos import houses as houses_repo
from risk.repos import pledge_modes as pmodes_repo
from risk.repos import semesters as semesters_repo
from risk.services import semester_archive as archive_svc

router = APIRouter(prefix="/semesters", tags=["semesters"])


@router.get("", response_model=list[SemesterOut])
def list_semesters(conn: sqlite3.Connection = Depends(get_conn)) -> list[SemesterOut]:
    return [SemesterOut.model_validate(s) for s in semesters_repo.list_all(conn)]


@router.post("", response_model=SemesterOut, status_code=201)
def create_semester(
    body: SemesterIn, conn: sqlite3.Connection = Depends(get_conn)
) -> SemesterOut:
    with service_errors(), transaction(conn):
        semesters_repo.insert(
            conn,
            name=body.name,
            starts_on=body.starts_on,
            ends_on=body.ends_on,
            pledge_takeover_starts_on=body.pledge_takeover_starts_on,
        )
    created = semesters_repo.get_by_name(conn, body.name)
    assert created is not None
    return SemesterOut.model_validate(created)


@router.post("/{name}/set-current", response_model=SemesterOut)
def set_current(name: str, conn: sqlite3.Connection = Depends(get_conn)) -> SemesterOut:
    with service_errors(), transaction(conn):
        semesters_repo.set_current(conn, name)
    sem = semesters_repo.get_by_name(conn, name)
    assert sem is not None
    return SemesterOut.model_validate(sem)


@router.get("/{name}/archive-check", response_model=ArchiveReportOut)
def archive_check(name: str, conn: sqlite3.Connection = Depends(get_conn)) -> ArchiveReportOut:
    """Preview archive blockers without committing."""
    with service_errors():
        sem = resolve_semester(conn, name)
    report = archive_svc.validate(conn, semester_id=sem.id)
    return ArchiveReportOut.model_validate(report)


@router.post("/{name}/archive", response_model=ArchiveResultOut)
def archive_semester(
    name: str, body: ArchiveIn, conn: sqlite3.Connection = Depends(get_conn)
) -> ArchiveResultOut:
    """Archive a non-current term. Destructive under force; carry_to preserves strikes."""
    with service_errors():
        sem = resolve_semester(conn, name)
        if sem.is_current:
            raise ValueError(
                f"{name!r} is the current term — set a different current semester before archiving."
            )
        if sem.archived_at is not None:
            raise ValueError(f"{name!r} is already archived ({sem.archived_at}).")
        carry_to_id: int | None = None
        if body.carry_to is not None:
            ct = semesters_repo.get_by_name(conn, body.carry_to)
            if ct is None:
                raise LookupError(f"carry-to semester {body.carry_to!r} not found")
            carry_to_id = ct.id
        with transaction(conn):
            result = archive_svc.archive(
                conn,
                semester_id=sem.id,
                archived_at=date.today().isoformat(),
                force=body.force,
                carry_to_semester_id=carry_to_id,
            )
    return ArchiveResultOut.model_validate(result)


@router.get("/{name}/house-modes", response_model=list[HouseModeOut])
def list_house_modes(
    name: str, conn: sqlite3.Connection = Depends(get_conn)
) -> list[HouseModeOut]:
    with service_errors():
        sem = resolve_semester(conn, name)
    rows = hss_repo.list_for_semester(conn, sem.id)
    return [
        HouseModeOut(house_slug=r.house_slug, pledge_mode_slug=r.pledge_mode_slug) for r in rows
    ]


@router.post("/{name}/house-modes", response_model=HouseModeOut)
def set_house_mode(
    name: str, body: HouseModeIn, conn: sqlite3.Connection = Depends(get_conn)
) -> HouseModeOut:
    """Pledge-takeover workflow: set a house's pledge mode for the semester."""
    with service_errors():
        sem = resolve_semester(conn, name)
        house = houses_repo.get_by_slug(conn, body.house_slug)
        if house is None:
            raise LookupError(f"house {body.house_slug!r} not found")
        mode = pmodes_repo.get(conn, body.pledge_mode_slug)
        if mode is None:
            raise LookupError(f"pledge mode {body.pledge_mode_slug!r} not found")
        with transaction(conn):
            hss_repo.set_mode(
                conn, house_id=house.id, semester_id=sem.id, pledge_mode_id=mode.id
            )
    return HouseModeOut(house_slug=body.house_slug, pledge_mode_slug=body.pledge_mode_slug)
