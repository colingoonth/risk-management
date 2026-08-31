"""GroupMe forwarder ops: is it running, what came in, and run it now.

The read-only side of the GroupMe feature. Everything here observes the
poll-and-forward loop that :mod:`risk.services.groupme_poll` runs from a
LaunchAgent every 120 seconds; nothing here posts to GroupMe.

Kept in its own module rather than alongside the identity and announce routes
because it has a different blast radius. These three endpoints cannot say
anything to the chapter — the worst a bug here can do is show a stale number.
The announce side can post to a group chat, and the contract makes it carry an
explicit ``confirm`` for that reason. Two audiences, two files.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.schemas import GroupMeHealthOut, GroupMeInboundOut
from risk.repos import groupme_inbound as inbound_repo
from risk.services import groupme_poll

router = APIRouter(prefix="/groupme", tags=["groupme"])


def _list_messages_override(request: Request) -> groupme_poll.ListMessages | None:
    """Test/dry-run seam, mirroring ``app.state.db_path``.

    ``create_app`` never sets this, so in every real process the poll route
    resolves the live client. A test that wants to force a cycle without a
    network sets ``app.state.groupme_list_messages`` to a stub.
    """
    return getattr(request.app.state, "groupme_list_messages", None)


@router.get("/health", response_model=GroupMeHealthOut)
def groupme_health(conn: sqlite3.Connection = Depends(get_conn)) -> GroupMeHealthOut:
    """Is the forwarder alive, and is every topic being read?

    Cheap and side-effect free — it reads the poll-state rows and the heartbeat
    marker, and never touches the network. Safe to poll from a dashboard.
    """
    with service_errors():
        report = groupme_poll.health(conn)
    return GroupMeHealthOut.model_validate(report)


@router.get("/inbound", response_model=list[GroupMeInboundOut])
def groupme_inbound(
    limit: int = Query(default=50, ge=1, le=500),
    triage: str | None = Query(default=None),
    group_slug: str | None = Query(default=None),
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[GroupMeInboundOut]:
    """Recent traffic, newest first.

    ``triage`` is validated in the repo against the stored vocabulary, so an
    unknown value comes back as a 400 naming the allowed ones rather than as an
    empty list that reads like a quiet night.
    """
    with service_errors():
        rows = inbound_repo.list_recent(conn, limit=limit, triage=triage, group_slug=group_slug)
    return [GroupMeInboundOut.model_validate(r) for r in rows]


@router.post("/poll", response_model=GroupMeHealthOut)
def groupme_poll_now(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
) -> GroupMeHealthOut:
    """Force one poll cycle and answer with the health payload.

    This is the "I just woke the laptop and do not want to wait 120 seconds"
    button, and it is the same cycle the LaunchAgent runs — including the push
    to cmux — so pressing it twice in a row is safe: every write behind it keys
    on the GroupMe message id.

    Returns health rather than the cycle's own counters because that is what the
    contract specifies and it is the more useful answer: the caller pressed the
    button to find out whether things are working, not to learn that this
    particular cycle read zero messages.
    """
    with service_errors():
        try:
            groupme_poll.poll_once(conn, list_messages=_list_messages_override(request))
        except ImportError as exc:
            # The HTTP client module is not installed in this build. Every other
            # route still works off stored state, so this is 503 (come back when
            # the client is there), not 500.
            raise HTTPException(
                status_code=503, detail=f"GroupMe client unavailable: {exc}"
            ) from exc
        report = groupme_poll.health(conn)
    return GroupMeHealthOut.model_validate(report)
