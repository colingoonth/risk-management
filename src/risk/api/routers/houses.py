"""Houses (event host houses) — drives the pledge-takeover panel."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.schemas import HouseOut
from risk.repos import houses as houses_repo

router = APIRouter(prefix="/houses", tags=["houses"])


@router.get("", response_model=list[HouseOut])
def list_houses(conn: sqlite3.Connection = Depends(get_conn)) -> list[HouseOut]:
    return [HouseOut.model_validate(h) for h in houses_repo.list_all(conn)]
