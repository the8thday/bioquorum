import itertools

import pytest

from bioquorum.core.status import (
    TRANSITIONS,
    aggregate,
    transition,
)
from bioquorum.core.status import (
    StatusTrigger as T,
)
from bioquorum.core.status import (
    VerificationStatus as S,
)


def test_every_pair_is_defined():
    for s, t in itertools.product(S, T):
        assert (s, t) in TRANSITIONS


@pytest.mark.parametrize(
    ("start", "trigger", "end"),
    [
        (S.UNVERIFIED, T.BINDINGS_RESOLVED, S.GROUNDED),
        (S.UNVERIFIED, T.BINDINGS_FAILED, S.DISPUTED),
        (S.GROUNDED, T.REVIEW_PASSED, S.REVIEWED),
        (S.GROUNDED, T.REVIEW_FAILED, S.DISPUTED),
        (S.REVIEWED, T.RERUN_MATCHED, S.REPRODUCED),
        (S.REVIEWED, T.RERUN_MISMATCHED, S.DISPUTED),
        (S.REPRODUCED, T.REVIEW_FAILED, S.DISPUTED),
        (S.DISPUTED, T.BINDINGS_RESOLVED, S.GROUNDED),
        # Levels cannot be skipped (ADR-015): an unbound claim that passes review is still
        # only an assertion, and a rerun never substitutes for grounding or review.
        (S.UNVERIFIED, T.REVIEW_PASSED, S.UNVERIFIED),
        (S.DISPUTED, T.REVIEW_PASSED, S.UNVERIFIED),
        (S.UNVERIFIED, T.RERUN_MATCHED, S.UNVERIFIED),
        (S.GROUNDED, T.RERUN_MATCHED, S.GROUNDED),
    ],
)
def test_transitions(start, trigger, end):
    assert transition(start, trigger) is end


def test_strings_are_accepted():
    assert transition("grounded", "review_passed") is S.REVIEWED  # type: ignore[arg-type]


def test_aggregate_is_the_weakest():
    assert aggregate([]) is S.UNVERIFIED
    assert aggregate([S.REPRODUCED, S.REVIEWED]) is S.REVIEWED
    assert aggregate([S.REPRODUCED, S.DISPUTED, S.REVIEWED]) is S.DISPUTED
    assert aggregate([S.REVIEWED, S.UNVERIFIED]) is S.UNVERIFIED
    assert aggregate([S.REVIEWED, S.GROUNDED]) is S.GROUNDED
    assert aggregate([S.GROUNDED, S.UNVERIFIED]) is S.UNVERIFIED


def test_upward_moves_climb_one_level_at_most():
    order = [S.UNVERIFIED, S.GROUNDED, S.REVIEWED, S.REPRODUCED]
    for (s, _t), end in TRANSITIONS.items():
        if s in order and end in order:
            assert order.index(end) <= order.index(s) + 1
