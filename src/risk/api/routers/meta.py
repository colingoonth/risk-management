"""Health + metadata."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk import __version__
from risk.api.deps import get_conn
from risk.api.schemas import SemesterOut, ShiftTypeWindowOut
from risk.repos import semesters as semesters_repo
from risk.repos import shift_type_windows as stw_repo

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/shift-type-windows", response_model=list[ShiftTypeWindowOut])
def shift_type_windows(
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[ShiftTypeWindowOut]:
    """Static reference data, six rows — when each shift type is actually worked.

    The calendar reads cleanup's +1-day offset from here rather than hardcoding
    it, so a migration that moves cleanup to +2 needs no frontend edit. Lives on
    the meta router because it is chapter-wide configuration, not per-semester.
    """
    return [ShiftTypeWindowOut.model_validate(w) for w in stw_repo.list_all(conn)]


@router.get("/meta")
def meta(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, object]:
    current = semesters_repo.get_current(conn)
    return {
        "version": __version__,
        "current_semester": SemesterOut.model_validate(current).model_dump()
        if current is not None
        else None,
    }
