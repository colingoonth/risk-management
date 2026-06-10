"""Member reads."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.schemas import MemberOut
from risk.repos import members as members_repo

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=list[MemberOut])
def list_members(
    status: str | None = None, conn: sqlite3.Connection = Depends(get_conn)
) -> list[MemberOut]:
    members = (
        members_repo.list_by_status(conn, status)
        if status is not None
        else members_repo.list_all(conn)
    )
    return [MemberOut.model_validate(m) for m in members]


@router.get("/{slug}", response_model=MemberOut)
def get_member(slug: str, conn: sqlite3.Connection = Depends(get_conn)) -> MemberOut:
    with service_errors():
        member = members_repo.resolve(conn, slug)
        if member is None:
            raise LookupError(f"member {slug!r} not found")
    return MemberOut.model_validate(member)
