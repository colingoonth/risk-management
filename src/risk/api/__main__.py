"""Run the API with uvicorn: ``python -m risk.api`` (or ``risk-api``).

Honours RISK_DB_PATH / XDG defaults via the per-request connection dependency,
so no DB path is passed here. Binds localhost only (single-user, ADR-014).
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("RISK_API_HOST", "127.0.0.1")
    port = int(os.environ.get("RISK_API_PORT", "8000"))
    uvicorn.run("risk.api:create_app", factory=True, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
