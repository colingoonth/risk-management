"""Event types — populates the create-event dropdown."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.schemas import EventTypeOut
from risk.repos import event_types as etypes_repo

router = APIRouter(prefix="/event-types", tags=["event-types"])


@router.get("", response_model=list[EventTypeOut])
def list_event_types(conn: sqlite3.Connection = Depends(get_conn)) -> list[EventTypeOut]:
    with_defaults = etypes_repo.ids_with_shift_defaults(conn)
    return [
        EventTypeOut(
            slug=et.slug,
            display_name=et.display_name,
            has_shift_defaults=et.id in with_defaults,
        )
        for et in etypes_repo.list_all(conn)
    ]
