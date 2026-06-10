"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from risk import __version__
from risk.api.routers import (
    dashboard,
    events,
    ingest,
    members,
    meta,
    semesters,
    strikes,
    swaps,
)

# Vite dev server origins — local-only, single-user (ADR-014).
_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def create_app(db_path: Path | str | None = None) -> FastAPI:
    """Build the API app. ``db_path`` (tests) overrides CLI-style path resolution."""
    app = FastAPI(title="Risk Management API", version=__version__)
    if db_path is not None:
        app.state.db_path = Path(db_path)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (meta, semesters, members, events, strikes, swaps, ingest, dashboard):
        app.include_router(module.router, prefix="/api")

    return app
