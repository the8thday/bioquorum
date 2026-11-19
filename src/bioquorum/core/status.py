"""Verification status state machine (ARCHITECTURE.md §5.7).

Status changes only through ``claim_status_changed`` / ``artifact_status_changed`` events,
and every transition goes through :func:`transition` so the rules live in one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class VerificationStatus(StrEnum):
    """The trust ladder (ADR-015)."""

    UNVERIFIED = "unverified"  # an assertion only
    GROUNDED = "grounded"  # every number and quote is bound to evidence (ADR-014)
    REVIEWED = "reviewed"
    REPRODUCED = "reproduced"
    REPLICATED = "replicated"
    DISPUTED = "disputed"


class StatusTrigger(StrEnum):
    BINDINGS_RESOLVED = "bindings_resolved"  # all bindings hold and nothing is left unbound
    BINDINGS_FAILED = "bindings_failed"  # a {{name}} or quote did not resolve
    REVIEW_PASSED = "review_passed"  # review finished with no ERROR-level findings
    REVIEW_FAILED = "review_failed"  # at least one ERROR-level finding
    RERUN_MATCHED = "rerun_matched"  # rerun outputs equal (within tolerance)
    RERUN_MISMATCHED = "rerun_mismatched"
    REPLICATION_MATCHED = "replication_matched"
    REPLICATION_MISMATCHED = "replication_mismatched"


# Lowest first. Used by aggregate(): a response is as trustworthy as its weakest claim.
_RANK: dict[VerificationStatus, int] = {
    VerificationStatus.DISPUTED: 0,
    VerificationStatus.UNVERIFIED: 1,
    VerificationStatus.GROUNDED: 2,
    VerificationStatus.REVIEWED: 3,
    VerificationStatus.REPRODUCED: 4,
    VerificationStatus.REPLICATED: 5,
}

S, T = VerificationStatus, StatusTrigger

# Every (status, trigger) pair is defined. Levels cannot be skipped (ADR-015): each passing
# trigger lifts a claim at most one level, and only from the level just below it.
TRANSITIONS: dict[tuple[VerificationStatus, StatusTrigger], VerificationStatus] = {
    (S.UNVERIFIED, T.BINDINGS_RESOLVED): S.GROUNDED,
    (S.UNVERIFIED, T.BINDINGS_FAILED): S.DISPUTED,
    (S.UNVERIFIED, T.REVIEW_PASSED): S.UNVERIFIED,  # reviewed, but still only an assertion
    (S.UNVERIFIED, T.REVIEW_FAILED): S.DISPUTED,
    (S.UNVERIFIED, T.RERUN_MATCHED): S.UNVERIFIED,
    (S.UNVERIFIED, T.RERUN_MISMATCHED): S.DISPUTED,
    (S.GROUNDED, T.BINDINGS_RESOLVED): S.GROUNDED,
    (S.GROUNDED, T.BINDINGS_FAILED): S.DISPUTED,
    (S.GROUNDED, T.REVIEW_PASSED): S.REVIEWED,
    (S.GROUNDED, T.REVIEW_FAILED): S.DISPUTED,
    (S.GROUNDED, T.RERUN_MATCHED): S.GROUNDED,
    (S.GROUNDED, T.RERUN_MISMATCHED): S.DISPUTED,
    (S.REVIEWED, T.BINDINGS_RESOLVED): S.REVIEWED,
    (S.REVIEWED, T.BINDINGS_FAILED): S.DISPUTED,
    (S.REVIEWED, T.REVIEW_PASSED): S.REVIEWED,
    (S.REVIEWED, T.REVIEW_FAILED): S.DISPUTED,
    (S.REVIEWED, T.RERUN_MATCHED): S.REPRODUCED,
    (S.REVIEWED, T.RERUN_MISMATCHED): S.DISPUTED,
    (S.REPRODUCED, T.BINDINGS_RESOLVED): S.REPRODUCED,
    (S.REPRODUCED, T.BINDINGS_FAILED): S.DISPUTED,
    (S.REPRODUCED, T.REVIEW_PASSED): S.REPRODUCED,
    (S.REPRODUCED, T.REVIEW_FAILED): S.DISPUTED,
    (S.REPRODUCED, T.RERUN_MATCHED): S.REPRODUCED,
    (S.REPRODUCED, T.RERUN_MISMATCHED): S.DISPUTED,
    # Leaving disputed means climbing again from the bottom: bindings first, then review.
    (S.DISPUTED, T.BINDINGS_RESOLVED): S.GROUNDED,
    (S.DISPUTED, T.BINDINGS_FAILED): S.DISPUTED,
    (S.DISPUTED, T.REVIEW_PASSED): S.UNVERIFIED,
    (S.DISPUTED, T.REVIEW_FAILED): S.DISPUTED,
    (S.DISPUTED, T.RERUN_MATCHED): S.DISPUTED,
    (S.DISPUTED, T.RERUN_MISMATCHED): S.DISPUTED,
}

for trigger in T:
    if trigger in (T.REPLICATION_MATCHED, T.REPLICATION_MISMATCHED):
        for status in S:
            TRANSITIONS[status, trigger] = (
                S.REPLICATED
                if trigger is T.REPLICATION_MATCHED and status is S.REPRODUCED
                else S.DISPUTED
                if trigger is T.REPLICATION_MISMATCHED and status in (S.REPRODUCED, S.REPLICATED)
                else status
            )
    else:
        TRANSITIONS[S.REPLICATED, trigger] = (
            S.DISPUTED
            if trigger in (T.BINDINGS_FAILED, T.REVIEW_FAILED, T.RERUN_MISMATCHED)
            else S.REPLICATED
        )


def transition(current: VerificationStatus, trigger: StatusTrigger) -> VerificationStatus:
    """Return the status after ``trigger`` is applied to ``current``."""
    return TRANSITIONS[(VerificationStatus(current), StatusTrigger(trigger))]


def aggregate(statuses: Iterable[VerificationStatus]) -> VerificationStatus:
    """The overall status of a set of claims: the lowest one. Empty -> unverified."""
    items = list(statuses)
    if not items:
        return VerificationStatus.UNVERIFIED
    return min(items, key=lambda s: _RANK[VerificationStatus(s)])
