"""Select the current completed review per answer from an ordered event history."""

from collections.abc import Iterable
from dataclasses import dataclass, field

from bioquorum.core import events as ev


@dataclass
class AnswerReview:
    claims: list[ev.ClaimExtracted] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    review_id: str | None = None


def answer_reviews(events: Iterable[ev.Event]) -> dict[str, AnswerReview]:
    current: dict[str, AnswerReview] = {}
    attempts: dict[str, tuple[str, AnswerReview]] = {}
    for event in events:
        payload = event.parsed()
        if isinstance(payload, ev.ReviewStarted):
            attempts[event.id] = (
                payload.target_event_id,
                AnswerReview(review_id=payload.review_id),
            )
        elif isinstance(payload, ev.ClaimExtracted):
            if attempt := attempts.get(event.causation_id or ""):
                attempt[1].claims.append(payload)
            else:
                current.setdefault(payload.source_event_id, AnswerReview()).claims.append(payload)
        elif isinstance(payload, ev.ReviewFinished):
            attempt = attempts.get(event.causation_id or "")
            if attempt and attempt[1].review_id == payload.review_id:
                target, review = attempt
                review.limitations = list(payload.limitations)
                if not review.claims and not review.limitations:
                    review.limitations.append("The reviewer returned no checkable claims.")
                current[target] = review
    return current
