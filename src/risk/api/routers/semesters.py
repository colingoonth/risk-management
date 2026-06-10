"""Semester CRUD + pledge-mode (pledge-takeover workflow)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import HouseModeIn, HouseModeOut, SemesterIn, SemesterOut
from risk.db.connection import transaction
from risk.repos import house_semester_status as hss_repo
from risk.repos import houses as houses_repo
from risk.repos import pledge_modes as pmodes_repo
from risk.repos import semesters as semesters_repo

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
