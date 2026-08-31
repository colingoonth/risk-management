"""FastAPI application factory."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from risk import __version__
from risk.api.routers import (
    dashboard,
    event_types,
    events,
    groupme,
    houses,
    ingest,
    members,
    meta,
    notes,
    semesters,
    shifts,
    strikes,
    swaps,
)

# Vite dev server origins — local-only, single-user (ADR-014). In the packaged
# (pywebview) build the SPA is served same-origin off this app, so CORS is moot
# there; these matter only for the two-process `vite dev` workflow.
_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _resolve_static_dir() -> Path | None:
    """Locate the built SPA bundle, if one exists.

    ``RISK_STATIC_DIR`` wins (set by the packaged launcher to the bundled
    location); otherwise fall back to the repo-relative ``web/dist`` produced
    by ``vite build``. Returns ``None`` when no bundle is present — the API
    then runs headless (dev mode, where Vite serves the SPA and proxies /api).
    """
    env = os.environ.get("RISK_STATIC_DIR")
    if env:
        p = Path(env).resolve()
        return p if p.is_dir() else None
    # src/risk/api/app.py -> repo root -> web/dist
    candidate = (Path(__file__).resolve().parents[3] / "web" / "dist").resolve()
    return candidate if candidate.is_dir() else None


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA same-origin: hashed assets verbatim, everything
    else falls back to ``index.html`` so client-side (react-router) deep links
    resolve. ``/api/*`` is owned by the routers and never reaches here."""
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:  # noqa: ARG001 — path is the route
        if full_path.startswith("api"):
            raise HTTPException(status_code=404)
        candidate = (static_dir / full_path).resolve()
        if full_path and static_dir in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")


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

    for module in (
        meta,
        semesters,
        members,
        events,
        event_types,
        houses,
        shifts,
        strikes,
        swaps,
        ingest,
        dashboard,
        notes,
        groupme,
    ):
        app.include_router(module.router, prefix="/api")

    # Same-origin SPA serving for the packaged build (registered last so the
    # catch-all never shadows the API routers).
    static_dir = _resolve_static_dir()
    if static_dir is not None:
        _mount_spa(app, static_dir)

    return app
