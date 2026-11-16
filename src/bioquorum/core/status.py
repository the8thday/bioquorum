"""Verification status state machine (ARCHITECTURE.md §5.7).

Status changes only through ``claim_status_changed`` / ``artifact_status_changed`` events,
and every transition goes through :func:`transition` so the rules live in one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    REVIEWED = "reviewed"
    REPRODUCED = "reproduced"
    DISPUTED = "disputed"


class StatusTrigger(StrEnum):
    REVIEW_PASSED = "review_passed"  # review finished with no ERROR-level findings
    REVIEW_FAILED = "review_failed"  # at least one ERROR-level finding
    RERUN_MATCHED = "rerun_matched"  # rerun outputs equal (within tolerance)
    RERUN_MISMATCHED = "rerun_mismatched"


# Lowest first. Used by aggregate(): a response is as trustworthy as its weakest claim.
_RANK: dict[VerificationStatus, int] = {
    VerificationStatus.DISPUTED: 0,
    VerificationStatus.UNVERIFIED: 1,
    VerificationStatus.REVIEWED: 2,
    VerificationStatus.REPRODUCED: 3,
}

S, T = VerificationStatus, StatusTrigger

# Every (status, trigger) pair is defined; rerun results never substitute for review.
TRANSITIONS: dict[tuple[VerificationStatus, StatusTrigger], VerificationStatus] = {
    (S.UNVERIFIED, T.REVIEW_PASSED): S.REVIEWED,
    (S.UNVERIFIED, T.REVIEW_FAILED): S.DISPUTED,
    (S.UNVERIFIED, T.RERUN_MATCHED): S.UNVERIFIED,
    (S.UNVERIFIED, T.RERUN_MISMATCHED): S.DISPUTED,
    (S.REVIEWED, T.REVIEW_PASSED): S.REVIEWED,
    (S.REVIEWED, T.REVIEW_FAILED): S.DISPUTED,
    (S.REVIEWED, T.RERUN_MATCHED): S.REPRODUCED,
    (S.REVIEWED, T.RERUN_MISMATCHED): S.DISPUTED,
    (S.REPRODUCED, T.REVIEW_PASSED): S.REPRODUCED,
    (S.REPRODUCED, T.REVIEW_FAILED): S.DISPUTED,
    (S.REPRODUCED, T.RERUN_MATCHED): S.REPRODUCED,
    (S.REPRODUCED, T.RERUN_MISMATCHED): S.DISPUTED,
    (S.DISPUTED, T.REVIEW_PASSED): S.REVIEWED,
    (S.DISPUTED, T.REVIEW_FAILED): S.DISPUTED,
    (S.DISPUTED, T.RERUN_MATCHED): S.DISPUTED,
    (S.DISPUTED, T.RERUN_MISMATCHED): S.DISPUTED,
}


def transition(current: VerificationStatus, trigger: StatusTrigger) -> VerificationStatus:
    """Return the status after ``trigger`` is applied to ``current``."""
    return TRANSITIONS[(VerificationStatus(current), StatusTrigger(trigger))]


def aggregate(statuses: Iterable[VerificationStatus]) -> VerificationStatus:
    """The overall status of a set of claims: the lowest one. Empty -> unverified."""
    items = list(statuses)
    if not items:
        return VerificationStatus.UNVERIFIED
    return min(items, key=lambda s: _RANK[VerificationStatus(s)])
