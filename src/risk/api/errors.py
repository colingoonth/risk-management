"""Translate service-layer exceptions into HTTP responses."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException


@contextmanager
def service_errors() -> Iterator[None]:
    """Map the service/repo exception vocabulary onto HTTP status codes.

    LookupError → 404 (missing entity), sqlite IntegrityError → 409 (constraint
    / duplicate), ValueError → 400 (bad input / illegal state transition).
    """
    try:
        yield
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
