"""Review one answer of a session end to end (ARCHITECTURE.md §6.5).

Evidence comes from the session's events; claims come from the reviewer model; then the
deterministic checks and citation_supports run and everything is recorded as events.
"""

from __future__ import annotations

from bioquorum.core import events as ev
from bioquorum.core.enums import Classification
from bioquorum.models.client import ModelClient
from bioquorum.review.checks import deterministic_checks
from bioquorum.review.checks.supports import CitationSupports
from bioquorum.review.claims import extract_claims
from bioquorum.review.evidence import Evidence
from bioquorum.review.service import ReviewResult, review
from bioquorum.storage.events import EventStore


class NothingToReviewError(LookupError):
    pass


async def review_answer(
    store: EventStore,
    client: ModelClient,
    *,
    session_id: str,
    classification: Classification,
    answer_event_id: str | None = None,
    use_model_checks: bool = True,
) -> ReviewResult:
    events = store.read(session_id)
    target = next(
        (
            e
            for e in reversed(events)
            if e.type == ev.AssistantMessage.event_type
            and (answer_event_id is None or e.id == answer_event_id)
        ),
        None,
    )
    if target is None:
        raise NothingToReviewError("no assistant answer to review in this session")
    payload = target.parsed()
    assert isinstance(payload, ev.AssistantMessage)
    answer = payload.message.text()
    if not answer.strip():
        raise NothingToReviewError("the answer has no text")
    prior = [e for e in events if e.seq < target.seq]
    evidence = Evidence.from_events(prior, answer=answer)
    claims = await extract_claims(
        client,
        answer,
        evidence,
        session_id=session_id,
        classification=classification,
        causation_id=target.id,
    )
    checks = list(deterministic_checks())
    if use_model_checks:
        checks.append(
            CitationSupports(client, session_id=session_id, classification=classification)
        )
    return await review(
        store,
        session_id=session_id,
        target_event_id=target.id,
        claims=claims,
        evidence=evidence,
        checks=checks,
        classification=classification,
    )
