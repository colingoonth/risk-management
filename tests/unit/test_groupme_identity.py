"""The mapper must never guess between two similar names.

Every name here is invented. The pair that matters is ``Marek Vantano`` and
``Marec Vantanno`` — two roster members whose names differ by one letter in each
half, which is the shape that makes a similarity score confident and wrong.
"""

from __future__ import annotations

import pytest

from risk.services.groupme import GroupMeMember
from risk.services.groupme_identity import (
    Candidate,
    build_plan,
    normalize,
    similar_candidates,
)


def _account(user_id: str, nickname: str) -> GroupMeMember:
    return GroupMeMember(user_id=user_id, membership_id=f"m{user_id}", nickname=nickname)


ALPHA = Candidate(member_id=1, display_name="Marek Vantano")
BRAVO = Candidate(member_id=2, display_name="Marec Vantanno")
CHARLIE = Candidate(member_id=3, display_name="Test Charlie")
ROSTER = [ALPHA, BRAVO, CHARLIE]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_normalize_folds_case_accents_and_intra_word_punctuation() -> None:
    assert normalize("Tést O'Alpha") == "test oalpha"
    assert normalize("TEST O’ALPHA") == "test oalpha"
    assert normalize("Test  O Alpha") == "test o alpha"
    assert normalize("Jean-Luc Alpha") == "jeanluc alpha"


def test_normalize_does_not_fold_two_different_names_together() -> None:
    """The whole point: near-misses must stay distinct keys."""
    assert normalize("Marek Vantano") != normalize("Marec Vantanno")
    assert normalize("Marek") != normalize("Marec")


# ---------------------------------------------------------------------------
# THE headline rule
# ---------------------------------------------------------------------------


def test_a_first_name_that_could_be_either_man_links_neither() -> None:
    plan = build_plan(
        roster=ROSTER, aliases=[], groupme_members=[_account("100", "Marek")]
    )
    assert plan.proposals == ()
    assert len(plan.ambiguous) == 1
    ambiguity = plan.ambiguous[0]
    assert {c.display_name for c in ambiguity.candidates} == {
        "Marek Vantano",
        "Marec Vantanno",
    }
    assert "confirm" in ambiguity.reason


def test_a_lone_near_candidate_is_still_not_a_match() -> None:
    """One candidate is the MOST dangerous case, not the safest.

    "Marec V" resembles only one roster member. A scoring matcher links it
    happily. This one refuses, because "resembles exactly one person I know
    about" is not the same fact as "is that person".
    """
    plan = build_plan(
        roster=[BRAVO, CHARLIE], aliases=[], groupme_members=[_account("100", "Marec V")]
    )
    assert plan.proposals == ()
    assert len(plan.ambiguous) == 1
    assert [c.display_name for c in plan.ambiguous[0].candidates] == ["Marec Vantanno"]


def test_both_similar_men_require_confirmation_even_together() -> None:
    """Two accounts, two near-misses, zero automatic links."""
    plan = build_plan(
        roster=ROSTER,
        aliases=[],
        groupme_members=[_account("100", "Marek V"), _account("200", "Marec V")],
    )
    assert plan.proposals == ()
    assert len(plan.ambiguous) == 2


def test_an_exact_full_name_match_links_and_leaves_the_lookalike_alone() -> None:
    plan = build_plan(
        roster=ROSTER, aliases=[], groupme_members=[_account("100", "Marek Vantano")]
    )
    assert len(plan.proposals) == 1
    proposal = plan.proposals[0]
    assert proposal.member_id == ALPHA.member_id
    assert proposal.confidence == "exact"
    assert BRAVO in plan.unmatched_members


def test_case_and_accent_differences_still_count_as_exact() -> None:
    plan = build_plan(
        roster=ROSTER, aliases=[], groupme_members=[_account("100", "  márek vantano ")]
    )
    assert [p.member_id for p in plan.proposals] == [ALPHA.member_id]
    assert plan.proposals[0].confidence == "exact"
    # The stored nickname is what GroupMe showed, not the normalised key.
    assert plan.proposals[0].nickname == "  márek vantano "


def test_a_registered_alias_links_at_the_alias_tier() -> None:
    plan = build_plan(
        roster=ROSTER,
        aliases=[(CHARLIE.member_id, "Chuck")],
        groupme_members=[_account("100", "Chuck")],
    )
    assert [p.confidence for p in plan.proposals] == ["alias"]
    assert plan.proposals[0].member_id == CHARLIE.member_id


def test_two_roster_members_with_the_same_name_link_neither() -> None:
    twin_a = Candidate(member_id=10, display_name="Test Delta")
    twin_b = Candidate(member_id=11, display_name="Test Delta")
    plan = build_plan(
        roster=[twin_a, twin_b], aliases=[], groupme_members=[_account("100", "Test Delta")]
    )
    assert plan.proposals == ()
    assert len(plan.ambiguous) == 1
    assert "2 roster members" in plan.ambiguous[0].reason


def test_two_accounts_matching_one_member_link_neither() -> None:
    """A duplicate GroupMe account is not a coin flip."""
    plan = build_plan(
        roster=ROSTER,
        aliases=[],
        groupme_members=[
            _account("100", "Test Charlie"),
            _account("200", "test charlie"),
        ],
    )
    assert plan.proposals == ()
    assert len(plan.ambiguous) == 2
    assert all("GroupMe accounts" in a.reason for a in plan.ambiguous)


def test_an_alias_shared_by_two_members_links_neither() -> None:
    plan = build_plan(
        roster=ROSTER,
        aliases=[(ALPHA.member_id, "Vanny"), (BRAVO.member_id, "Vanny")],
        groupme_members=[_account("100", "Vanny")],
    )
    assert plan.proposals == ()
    assert len(plan.ambiguous) == 1


def test_an_account_nothing_resembles_is_unmatched_not_ambiguous() -> None:
    plan = build_plan(
        roster=ROSTER, aliases=[], groupme_members=[_account("100", "Zzyzx Quorlblat")]
    )
    assert plan.proposals == ()
    assert plan.ambiguous == ()
    assert plan.unmatched_nicknames == (("100", "Zzyzx Quorlblat"),)


def test_a_blank_nickname_is_reported_never_matched() -> None:
    plan = build_plan(roster=ROSTER, aliases=[], groupme_members=[_account("100", "   ")])
    assert plan.proposals == ()
    assert len(plan.ambiguous) == 1
    assert "no usable nickname" in plan.ambiguous[0].reason


def test_roster_members_with_no_account_are_reported_as_unmatched() -> None:
    plan = build_plan(roster=ROSTER, aliases=[], groupme_members=[])
    assert {c.member_id for c in plan.unmatched_members} == {1, 2, 3}


# ---------------------------------------------------------------------------
# Candidate generation is display-only
# ---------------------------------------------------------------------------


def test_similar_candidates_surfaces_both_lookalikes() -> None:
    found = similar_candidates("Marek", ROSTER)
    assert {c.display_name for c in found} == {"Marek Vantano", "Marec Vantanno"}


def test_similar_candidates_ignores_a_two_letter_coincidence() -> None:
    """A shared prefix shorter than the threshold is noise, not a suggestion."""
    assert similar_candidates("Ma", ROSTER) == ()


@pytest.mark.parametrize("nickname", ["", "   ", "!!!"])
def test_similar_candidates_of_an_empty_name_is_empty(nickname: str) -> None:
    assert similar_candidates(nickname, ROSTER) == ()
