"""Health + metadata."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk import __version__
from risk.api.deps import get_conn
from risk.api.schemas import SemesterOut
from risk.repos import semesters as semesters_repo

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/meta")
def meta(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, object]:
    current = semesters_repo.get_current(conn)
    return {
        "version": __version__,
        "current_semester": SemesterOut.model_validate(current).model_dump()
        if current is not None
        else None,
    }
