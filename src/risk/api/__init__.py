"""HTTP API layer (FastAPI) over the risk service + repo layers.

A thin adapter — every endpoint calls the same already-tested service/repo
functions the CLI uses, and runs entirely locally (ADR-014: no paid runtime).
Sits at the same dependency layer as ``cli/*``: it imports services + repos and
is imported by nothing in the core graph.
"""

from __future__ import annotations

from risk.api.app import create_app

__all__ = ["create_app"]
