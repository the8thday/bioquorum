"""Review one answer of a session end to end (ARCHITECTURE.md §6.5).

Evidence comes from the session's events; claims come from the reviewer model; then the
deterministic checks and citation_supports run and everything is recorded as events.
"""

from __future__ import annotations

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification
from bioquorum.models.client import ModelClient
from bioquorum.review.checks import deterministic_checks
from bioquorum.review.checks.methods import MethodSanity
from bioquorum.review.checks.supports import CitationSupports
from bioquorum.review.claims import extract_claims
from bioquorum.review.evidence import Evidence
from bioquorum.review.service import ReviewResult, review
from bioquorum.storage.events import EventStore
from bioquorum.tools.context import ToolContext


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
    method_sanity: bool = False,
    online_citations: ToolContext | None = None,
) -> ReviewResult:
    events = store.history(session_id)
    target = next(
        (
            e
            for e in reversed(events)
            if e.type == ev.AssistantMessage.event_type
            and e.session_id == session_id
            and (answer_event_id is None or e.id == answer_event_id)
            and isinstance(message := e.parsed(), ev.AssistantMessage)
            and not message.message.tool_calls()
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
    if method_sanity:
        latest = next(
            (
                p.message.text()
                for event in reversed(prior)
                if event.actor is Actor.USER and isinstance(p := event.parsed(), ev.UserMessage)
            ),
            evidence.question,
        )
        evidence.question = latest

    claims = await extract_claims(
        client,
        answer,
        evidence,
        session_id=session_id,
        classification=classification,
        causation_id=target.id,
    )
    checks = deterministic_checks(online_citations, causation_id=target.id)
    if use_model_checks:
        checks.append(
            CitationSupports(client, session_id=session_id, classification=classification)
        )
    if use_model_checks and method_sanity:
        checks.append(MethodSanity(client, session_id, classification))
    result = await review(
        store,
        session_id=session_id,
        target_event_id=target.id,
        claims=claims,
        evidence=evidence,
        checks=checks,
        classification=classification,
    )
    if not claims:
        result.errors.append("claim_extraction: the reviewer returned no checkable claims")
    return result
