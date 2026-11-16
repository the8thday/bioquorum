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
        (S.UNVERIFIED, T.REVIEW_PASSED, S.REVIEWED),
        (S.REVIEWED, T.RERUN_MATCHED, S.REPRODUCED),
        (S.REVIEWED, T.RERUN_MISMATCHED, S.DISPUTED),
        (S.REPRODUCED, T.REVIEW_FAILED, S.DISPUTED),
        (S.DISPUTED, T.REVIEW_PASSED, S.REVIEWED),
        (S.UNVERIFIED, T.RERUN_MATCHED, S.UNVERIFIED),  # rerun never substitutes review
    ],
)
def test_transitions(start, trigger, end):
    assert transition(start, trigger) is end


def test_strings_are_accepted():
    assert transition("unverified", "review_passed") is S.REVIEWED  # type: ignore[arg-type]


def test_aggregate_is_the_weakest():
    assert aggregate([]) is S.UNVERIFIED
    assert aggregate([S.REPRODUCED, S.REVIEWED]) is S.REVIEWED
    assert aggregate([S.REPRODUCED, S.DISPUTED, S.REVIEWED]) is S.DISPUTED
    assert aggregate([S.REVIEWED, S.UNVERIFIED]) is S.UNVERIFIED
