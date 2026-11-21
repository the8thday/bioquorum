"""Outstanding material reviews, derived independently from claim trust grades."""

from collections.abc import Iterable

from bioquorum.core import events as ev


def pending_reviews(
    events: Iterable[ev.Event], project_id: str, program_id: str
) -> dict[str, set[str]]:
    outstanding: dict[str, set[str]] = {}
    for event in events:
        if event.type not in (ev.ClaimReviewFlagged.event_type, ev.ClaimReviewCleared.event_type):
            continue
        if (
            event.session_id is not None
            or event.payload.get("project_id") != project_id
            or event.payload.get("program_id") != program_id
        ):
            continue
        payload = event.parsed()
        if isinstance(payload, ev.ClaimReviewFlagged):
            outstanding.setdefault(payload.claim_id, set()).add(payload.material_version_id)
        elif isinstance(payload, ev.ClaimReviewCleared):
            versions = outstanding.get(payload.claim_id)
            if versions:
                versions.discard(payload.material_version_id)
                if not versions:
                    del outstanding[payload.claim_id]
    return outstanding


def replacement_claims(
    events: Iterable[ev.Event], project_id: str, program_id: str
) -> dict[str, tuple[str, str]]:
    """Link each original statement to its most recent corrected measurements."""
    sessions = {}
    result = {}
    for event in events:
        if (
            event.session_id is not None
            or event.payload.get("project_id") != project_id
            or event.payload.get("program_id") != program_id
        ):
            continue
        if event.type not in (ev.RecomputeStarted.event_type, ev.RecomputeFinished.event_type):
            continue
        p = event.parsed()
        if isinstance(p, ev.RecomputeStarted):
            sessions[p.recompute_id] = p.session_id
        elif (
            isinstance(p, ev.RecomputeFinished)
            and p.status == "completed"
            and p.recompute_id in sessions
        ):
            for comparison in p.comparisons:
                if comparison.new_claim_id:
                    result[comparison.old_claim_id] = (
                        comparison.new_claim_id,
                        sessions[p.recompute_id],
                    )
    return result
