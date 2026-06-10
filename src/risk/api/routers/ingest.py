"""Roster ingest (Google Form CSV upload)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.db.connection import transaction
from risk.services import ingest as ingest_svc

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _summary(preview: ingest_svc.GformPreview) -> dict[str, object]:
    return {
        "semester": preview.semester_name,
        "base_year": preview.base_year,
        "total_rows": len(preview.rows),
        "new_members": len(preview.new_members),
        "existing_members": len(preview.existing_members),
        "exec_assignments": len(preview.exec_assignments),
        "unmapped_rising_class": list(preview.unmapped_rising_class),
        "unmapped_pledge_class": list(preview.unmapped_pledge_class),
    }


@router.post("/gform-roster")
def gform_roster(
    semester: str = Form(...),
    dry_run: bool = Form(default=False),
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, object]:
    """Upload a Google Form roster CSV and ingest it (or preview with dry_run)."""
    raw = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=True) as tmp:
        tmp.write(raw)
        tmp.flush()
        path = Path(tmp.name)
        with service_errors():
            if dry_run:
                preview = ingest_svc.preview_gform_roster(
                    conn, path=path, semester_name=semester
                )
                return {"preview": _summary(preview), "dry_run": True}
            with transaction(conn):
                result = ingest_svc.apply_gform_roster(
                    conn, path=path, semester_name=semester
                )
    return {
        "preview": _summary(result.preview),
        "inserted_members": len(result.inserted_member_ids),
        "exec_roles_set": result.exec_roles_set,
    }
