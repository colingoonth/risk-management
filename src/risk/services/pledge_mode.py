"""Pledge-mode resolution.

Three configured modes (per ``pledge_modes`` seed):
  - ``normal``                  → brothers-only
  - ``pledge_takeover_partial`` → pledges fill some slots, brothers fill the rest
  - ``pledge_takeover_full``    → pledges fill all slots

Auto-downgrade rules (per canonical plan §4.2):
  - ``pledge_takeover_full`` → ``pledge_takeover_partial`` if
    ``eligible_pledges < total_required`` (not enough pledges to fill).
  - ``pledge_takeover_partial`` → ``normal`` if ``eligible_pledges == 0``.

No upgrades. ``normal`` stays ``normal`` regardless of pledge count — the
chair must explicitly set a takeover mode on ``house_semester_status`` to
opt a house into pledge takeover. (This deliberately omits the "normal
→ partial after takeover date" auto-upgrade from the earlier draft; the
chair-driven config is simpler and avoids surprising auto-assignments to
pledges that the chair didn't ask for.)

If ``house_semester_status`` has no row for the (host_house, semester), the
configured mode defaults to ``normal``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from risk.repos import house_semester_status as hss_repo
from risk.repos import pledge_modes as pmodes_repo

MODE_NORMAL = "normal"
MODE_PARTIAL = "pledge_takeover_partial"
MODE_FULL = "pledge_takeover_full"


@dataclass(frozen=True, slots=True)
class ResolvedMode:
    configured_slug: str
    resolved_slug: str
    resolved_id: int
    eligible_pledges: int
    eligible_brothers: int
    total_required: int


def _downgrade(configured: str, eligible_pledges: int, total_required: int) -> str:
    if configured == MODE_FULL:
        if eligible_pledges < total_required:
            return _downgrade(MODE_PARTIAL, eligible_pledges, total_required)
        return MODE_FULL
    if configured == MODE_PARTIAL:
        if eligible_pledges == 0:
            return MODE_NORMAL
        return MODE_PARTIAL
    return MODE_NORMAL


def resolve(
    conn: sqlite3.Connection,
    *,
    host_house_id: int | None,
    semester_id: int,
    eligible_pledges: int,
    eligible_brothers: int,
    total_required: int,
) -> ResolvedMode:
    """Resolve the effective pledge mode for an event.

    ``host_house_id = None`` (off-site event) skips house lookup and uses
    ``normal``. The configured mode comes from ``house_semester_status``;
    auto-downgrades apply based on eligible pledge count vs required slots.
    """
    if host_house_id is None:
        configured = MODE_NORMAL
    else:
        hss = hss_repo.get(conn, house_id=host_house_id, semester_id=semester_id)
        configured = hss.pledge_mode_slug if hss is not None else MODE_NORMAL

    resolved = _downgrade(configured, eligible_pledges, total_required)
    resolved_pm = pmodes_repo.get(conn, resolved)
    if resolved_pm is None:
        raise LookupError(f"pledge_mode {resolved!r} not seeded")
    return ResolvedMode(
        configured_slug=configured,
        resolved_slug=resolved,
        resolved_id=resolved_pm.id,
        eligible_pledges=eligible_pledges,
        eligible_brothers=eligible_brothers,
        total_required=total_required,
    )
