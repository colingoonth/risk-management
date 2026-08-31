"""Match the roster to GroupMe accounts without ever guessing.

The whole module exists to make one behaviour structural rather than
aspirational: **two similar names on one roster must both reach a human.**

A chapter roster reliably contains near-collisions — two men whose first names
differ by a letter, a pair of cousins, someone who goes by his surname. Any
similarity score is at its most confident exactly there, and being wrong costs
either an @ aimed at the wrong brother in front of the chapter, or the right one
removed from the group the night before he works door.

So there are exactly two automatic tiers, and both are EQUALITY, not similarity:

    exact   normalised GroupMe nickname == normalised roster display name
    alias   normalised GroupMe nickname == a normalised registered member alias

Both are additionally required to be UNIQUE IN BOTH DIRECTIONS. One nickname
matching two roster members is ambiguous; one roster member matched by two
GroupMe accounts is ambiguous. Ambiguity never resolves itself — it is reported
with its candidates and waits for a person.

A link that EXISTS can also stop being usable — see :func:`blocked_identities`.
A GroupMe user id is stable while the nickname it was matched on is not, so an
account that has been renamed, or that now shares a nickname with somebody else,
is blocked from every write until a human re-confirms it. The rename is never
accepted quietly: refreshing the cached nickname on our own is precisely how a
stable id ends up permanently welded to the wrong brother.

Everything that is not one of those two equalities is a CANDIDATE, never a
match, no matter how obvious it looks. ``similar_candidates`` exists purely to
give the human a short list to pick from; nothing downstream may promote a
candidate on its own.

Normalisation folds case, strips accents, and drops apostrophes and hyphens —
"O'Rourke" and "ORourke" are the same person typing his own name twice, and no
chapter has ever had two brothers who differ only by an apostrophe. It does NOT
fold vowels, drop letters, or measure edit distance, because those are the
operations that turn two different people into one.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from risk.repos import groupme_identities as identities_repo
from risk.repos import members as members_repo
from risk.services.groupme import GroupMeMember

MIN_SHARED_PREFIX = 3
"""How many leading characters two name tokens must share to be worth showing a
human as a possible pair. Purely a display threshold — a shared prefix never
links anything."""

# Characters people put inside a single name token. Removed rather than turned
# into a separator, so "O'Rourke" == "ORourke" and "Jean-Luc" == "JeanLuc",
# while "Jean Luc" stays two tokens (which it is).
_INTRA_WORD_PUNCT = "'’ʼ-‐‑."


@dataclass(frozen=True, slots=True)
class Candidate:
    member_id: int
    display_name: str


@dataclass(frozen=True, slots=True)
class Proposal:
    """An automatic link. Only ever ``exact`` or ``alias``."""

    member_id: int
    display_name: str
    groupme_user_id: str
    nickname: str
    confidence: str


@dataclass(frozen=True, slots=True)
class Ambiguity:
    """Something a person has to decide. Never applied automatically."""

    groupme_user_id: str | None
    nickname: str | None
    candidates: tuple[Candidate, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class Drift:
    """A linked account whose GroupMe nickname no longer matches what we stored."""

    member_id: int
    display_name: str
    groupme_user_id: str
    stored_nickname: str | None
    current_nickname: str


@dataclass(frozen=True, slots=True)
class MappingPlan:
    proposals: tuple[Proposal, ...]
    ambiguous: tuple[Ambiguity, ...]
    unmatched_members: tuple[Candidate, ...]
    unmatched_nicknames: tuple[tuple[str, str], ...]
    """``(groupme_user_id, nickname)`` for accounts nothing on the roster resembles."""
    drift: tuple[Drift, ...]


# ---------------------------------------------------------------------------
# Normalisation + candidate generation (pure, no database)
# ---------------------------------------------------------------------------


def normalize(name: str) -> str:
    """Fold a name to its comparison key.

    Accent-stripping is NFKD + drop combining marks, so a nickname typed with a
    composed "é" and a roster row typed with "e" + combining acute are the same
    key. They are visually identical and no human would call them two people.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    folded = without_marks.casefold()
    cleaned = []
    for ch in folded:
        if ch in _INTRA_WORD_PUNCT:
            continue
        cleaned.append(ch if (ch.isalnum() or ch.isspace()) else " ")
    return " ".join("".join(cleaned).split())


def tokens(name: str) -> tuple[str, ...]:
    return tuple(normalize(name).split())


def _looks_related(a: str, b: str) -> bool:
    """Do two normalised name tokens share enough to be worth a human's glance?"""
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < MIN_SHARED_PREFIX:
        return False
    shared = 0
    for x, y in zip(shorter, longer, strict=False):
        if x != y:
            break
        shared += 1
    return shared >= MIN_SHARED_PREFIX


def similar_candidates(nickname: str, roster: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """Roster members a human might plausibly mean by ``nickname``.

    A SUGGESTION LIST, and nothing else. Returning exactly one candidate is not
    a match and callers must not treat it as one — a lone candidate is the most
    dangerous case there is, because it is where a "surely that's him" shortcut
    feels safest and is least checkable.
    """
    nick_tokens = tokens(nickname)
    if not nick_tokens:
        return ()
    out: list[Candidate] = []
    for member in roster:
        member_tokens = tokens(member.display_name)
        if any(_looks_related(n, m) for n in nick_tokens for m in member_tokens):
            out.append(member)
    return tuple(sorted(out, key=lambda c: (c.display_name, c.member_id)))


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def build_plan(
    *,
    roster: Sequence[Candidate],
    aliases: Iterable[tuple[int, str]],
    groupme_members: Sequence[GroupMeMember],
    existing: Sequence[identities_repo.GroupMeIdentity] = (),
) -> MappingPlan:
    """Work out what can be linked automatically and what cannot.

    ``aliases`` is ``(member_id, alias)`` pairs from ``member_aliases``.
    ``existing`` is the links already in the database; those members and those
    accounts are removed from consideration and only checked for nickname drift.

    Pure: takes plain data, returns plain data, writes nothing.
    """
    by_member_id = {c.member_id: c for c in roster}
    linked_member_ids = {e.member_id for e in existing}
    linked_user_ids = {e.groupme_user_id for e in existing}

    drift: list[Drift] = []
    for link in existing:
        current = next(
            (g for g in groupme_members if g.user_id == link.groupme_user_id),
            None,
        )
        if current is None:
            continue
        if link.nickname is not None and normalize(link.nickname) != normalize(current.nickname):
            member = by_member_id.get(link.member_id)
            drift.append(
                Drift(
                    member_id=link.member_id,
                    display_name=member.display_name if member else "",
                    groupme_user_id=link.groupme_user_id,
                    stored_nickname=link.nickname,
                    current_nickname=current.nickname,
                )
            )

    open_roster = [c for c in roster if c.member_id not in linked_member_ids]
    open_accounts = [
        g for g in groupme_members if g.user_id not in linked_user_ids and g.user_id
    ]

    exact_index: dict[str, list[Candidate]] = defaultdict(list)
    for member in open_roster:
        exact_index[normalize(member.display_name)].append(member)

    alias_index: dict[str, list[Candidate]] = defaultdict(list)
    for member_id, alias in aliases:
        member = by_member_id.get(member_id)
        if member is None or member.member_id in linked_member_ids:
            continue
        key = normalize(alias)
        if key and member not in alias_index[key]:
            alias_index[key].append(member)

    proposals: list[Proposal] = []
    ambiguous: list[Ambiguity] = []

    for account in open_accounts:
        key = normalize(account.nickname)
        if not key:
            ambiguous.append(
                Ambiguity(
                    groupme_user_id=account.user_id,
                    nickname=account.nickname,
                    candidates=(),
                    reason="this GroupMe account has no usable nickname",
                )
            )
            continue

        hits = exact_index.get(key) or []
        tier = "exact"
        if not hits:
            hits = alias_index.get(key) or []
            tier = "alias"

        if len(hits) == 1:
            proposals.append(
                Proposal(
                    member_id=hits[0].member_id,
                    display_name=hits[0].display_name,
                    groupme_user_id=account.user_id,
                    nickname=account.nickname,
                    confidence=tier,
                )
            )
            continue

        if len(hits) > 1:
            ambiguous.append(
                Ambiguity(
                    groupme_user_id=account.user_id,
                    nickname=account.nickname,
                    candidates=tuple(sorted(hits, key=lambda c: (c.display_name, c.member_id))),
                    reason=(
                        f"{len(hits)} roster members answer to this name — "
                        "confirm which one this account belongs to"
                    ),
                )
            )
            continue

        near = similar_candidates(account.nickname, open_roster)
        if near:
            ambiguous.append(
                Ambiguity(
                    groupme_user_id=account.user_id,
                    nickname=account.nickname,
                    candidates=near,
                    reason=(
                        "no exact or alias match; "
                        f"{len(near)} roster name(s) resemble it and need confirming"
                    ),
                )
            )

    # Reverse uniqueness. Two accounts proposing the same member is a duplicate
    # GroupMe account, an old one and a new one, or two people sharing a name we
    # have already folded — all three want a human, none wants a coin flip.
    per_member: dict[int, list[Proposal]] = defaultdict(list)
    for proposal in proposals:
        per_member[proposal.member_id].append(proposal)
    kept: list[Proposal] = []
    for member_id, group in per_member.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        member = by_member_id.get(member_id)
        for proposal in group:
            ambiguous.append(
                Ambiguity(
                    groupme_user_id=proposal.groupme_user_id,
                    nickname=proposal.nickname,
                    candidates=((member,) if member else ()),
                    reason=(
                        f"{len(group)} GroupMe accounts match this roster member — "
                        "confirm which one is current"
                    ),
                )
            )

    matched_member_ids = {p.member_id for p in kept}
    matched_user_ids = {p.groupme_user_id for p in kept}
    reported_user_ids = matched_user_ids | {
        a.groupme_user_id for a in ambiguous if a.groupme_user_id
    }

    unmatched_members = tuple(
        sorted(
            (c for c in open_roster if c.member_id not in matched_member_ids),
            key=lambda c: (c.display_name, c.member_id),
        )
    )
    unmatched_nicknames = tuple(
        (g.user_id, g.nickname) for g in open_accounts if g.user_id not in reported_user_ids
    )

    return MappingPlan(
        proposals=tuple(sorted(kept, key=lambda p: (p.display_name, p.member_id))),
        ambiguous=tuple(
            sorted(ambiguous, key=lambda a: (a.nickname or "", a.groupme_user_id or ""))
        ),
        unmatched_members=unmatched_members,
        unmatched_nicknames=unmatched_nicknames,
        drift=tuple(sorted(drift, key=lambda d: d.display_name)),
    )


# ---------------------------------------------------------------------------
# Database-facing layer
# ---------------------------------------------------------------------------


def roster_candidates(
    conn: sqlite3.Connection, *, status_slug: str = "active"
) -> list[Candidate]:
    return [
        Candidate(member_id=m.id, display_name=m.display_name)
        for m in members_repo.list_by_status(conn, status_slug)
    ]


def roster_aliases(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    rows = conn.execute("SELECT member_id, alias FROM member_aliases").fetchall()
    return [(int(r["member_id"]), str(r["alias"])) for r in rows]


def plan_for_roster(
    conn: sqlite3.Connection,
    groupme_members: Sequence[GroupMeMember],
    *,
    status_slug: str = "active",
) -> MappingPlan:
    """Build the plan from the database plus a live member list."""
    return build_plan(
        roster=roster_candidates(conn, status_slug=status_slug),
        aliases=roster_aliases(conn),
        groupme_members=groupme_members,
        existing=identities_repo.list_all(conn),
    )


def apply_proposals(
    conn: sqlite3.Connection,
    proposals: Sequence[Proposal],
    *,
    linked_at: str | None = None,
) -> int:
    """Write the automatic links. Caller supplies the transaction.

    Only ever called with ``build_plan``'s ``proposals`` — the ambiguous list has
    no code path into this function, which is what stops "just apply the single
    candidate" from becoming a one-line change someone makes on a Thursday.
    """
    stamp = linked_at or datetime.now(UTC).isoformat(timespec="seconds")
    for proposal in proposals:
        identities_repo.link(
            conn,
            member_id=proposal.member_id,
            groupme_user_id=proposal.groupme_user_id,
            nickname=proposal.nickname,
            confidence=proposal.confidence,
            linked_at=stamp,
        )
    return len(proposals)


def confirm_link(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    groupme_user_id: str,
    nickname: str | None,
    linked_at: str | None = None,
) -> None:
    """Record a link a human made. Always stored as ``confirmed``.

    Confidence is not a parameter here on purpose: this function is reached only
    from a person naming both sides, and letting a caller pass ``exact`` would
    let a guess launder itself as an equality match.
    """
    if members_repo.get_by_id(conn, member_id) is None:
        raise LookupError(f"member {member_id} not found")
    identities_repo.link(
        conn,
        member_id=member_id,
        groupme_user_id=groupme_user_id,
        nickname=nickname,
        confidence="confirmed",
        linked_at=linked_at or datetime.now(UTC).isoformat(timespec="seconds"),
    )


BLOCK_DUPLICATE_NICKNAME = "duplicate-named"
BLOCK_DRIFTED = "drifted"
BLOCK_NO_NICKNAME = "no-nickname"
BLOCK_NOT_IN_GROUP = "not-in-parent-group"


@dataclass(frozen=True, slots=True)
class Block:
    """A linked identity that may not be used for a write.

    "Used for a write" means mentioned in an announcement, added to the group,
    or removed from it. All three act on a specific human being, and every
    reason below means we are no longer certain which one.
    """

    groupme_user_id: str
    member_id: int
    display_name: str
    """The ROSTER name — who we believe this is."""
    reason: str
    detail: str
    current_nickname: str | None = None
    """What GroupMe shows for this account right now, when we could ask. For a
    drifted link this is the new name, and it is what a chair needs to see to
    decide whether to confirm the rename — the roster name is the half he
    already knows."""


def blocked_identities(
    conn: sqlite3.Connection,
    *,
    present: Sequence[GroupMeMember] | None = None,
) -> dict[str, Block]:
    """Linked identities that must NOT be mentioned, added or removed.

    Keyed by ``groupme_user_id``. Four reasons, and the common thread is that a
    GroupMe user id is STABLE while the human attached to it is asserted by a
    nickname that is not:

    ``duplicate-named``  two linked identities now show the same nickname. The
        message would read as tagging one man twice and the wrong one once.
    ``drifted``          the account has been renamed since it was linked. The
        rename is not evidence of who it is; it is evidence that the thing we
        matched on has moved. Accepting it silently is exactly how a stable user
        id becomes permanently welded to the wrong brother.
    ``no-nickname``      nothing to render after the ``@``.
    ``not-in-parent-group``  the account is not in the Risk group any more, so a
        mention reaches nobody and a removal has nothing to remove. Only
        detectable when ``present`` is supplied.

    ``present=None`` means "we could not ask GroupMe" — the DB-derivable blocks
    still apply and the two live ones are simply not evaluated. That is the
    honest degraded answer for an offline preview, and callers say so.
    """
    linked = identities_repo.list_linked(conn)
    blocks: dict[str, Block] = {}

    by_nickname: dict[str, list[identities_repo.LinkedMember]] = defaultdict(list)
    for link in linked:
        if link.nickname is None or not link.nickname.strip():
            blocks[link.groupme_user_id] = Block(
                groupme_user_id=link.groupme_user_id,
                member_id=link.member_id,
                display_name=link.display_name,
                reason=BLOCK_NO_NICKNAME,
                detail="no GroupMe nickname stored for this link",
            )
            continue
        by_nickname[normalize(link.nickname)].append(link)

    for key, group in by_nickname.items():
        if len(group) < 2:
            continue
        names = ", ".join(sorted(link.display_name for link in group))
        for link in group:
            blocks[link.groupme_user_id] = Block(
                groupme_user_id=link.groupme_user_id,
                member_id=link.member_id,
                display_name=link.display_name,
                reason=BLOCK_DUPLICATE_NICKNAME,
                detail=f"{len(group)} linked members share the nickname {key!r}: {names}",
            )

    if present is None:
        return blocks

    live = {account.user_id: account for account in present}
    for link in linked:
        if link.groupme_user_id in blocks:
            continue
        account = live.get(link.groupme_user_id)
        if account is None:
            blocks[link.groupme_user_id] = Block(
                groupme_user_id=link.groupme_user_id,
                member_id=link.member_id,
                display_name=link.display_name,
                reason=BLOCK_NOT_IN_GROUP,
                detail="this account is not currently in the parent group",
                current_nickname=None,
            )
            continue
        if link.nickname is not None and normalize(link.nickname) != normalize(account.nickname):
            blocks[link.groupme_user_id] = Block(
                groupme_user_id=link.groupme_user_id,
                member_id=link.member_id,
                display_name=link.display_name,
                reason=BLOCK_DRIFTED,
                detail=(
                    f"linked as {link.nickname!r}, now shows as {account.nickname!r} — "
                    "re-confirm before using this link"
                ),
                current_nickname=account.nickname,
            )
    return blocks


def confirm_rename(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    new_nickname: str,
    linked_at: str | None = None,
) -> None:
    """Accept a rename, as a HUMAN DECISION. Never called automatically.

    This deliberately does not exist as an ``accept_all_drift`` sweep, and the
    mapper does not call it. A renamed account is the one case where the thing
    the link was made on has changed and the id has not, so refreshing the cached
    nickname on our own turns "the name we matched no longer holds" into "the
    name we matched is whatever it says today" — which is a link that can never
    be wrong and therefore never useful.

    Re-confirming stores ``confirmed``: whatever tier the link was made at, the
    evidence for it now is that a person looked.
    """
    if not new_nickname.strip():
        raise ValueError("a confirmed rename needs the new nickname")
    existing = identities_repo.get_for_member(conn, member_id)
    if existing is None:
        raise LookupError(f"member {member_id} has no GroupMe link to re-confirm")
    identities_repo.link(
        conn,
        member_id=member_id,
        groupme_user_id=existing.groupme_user_id,
        nickname=new_nickname,
        confidence="confirmed",
        linked_at=linked_at or datetime.now(UTC).isoformat(timespec="seconds"),
    )
