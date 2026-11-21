"""Resolve the optional discussion items to exact excerpts of a recorded first-pass answer."""

import re

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.review.binding import render
from bioquorum.review.evidence import Evidence

HEADINGS = ("Judgment", "Strongest counterevidence", "Disagreement", "Next step")
HEADING = re.compile(
    r"^\s*(?:#{1,4}\s+)?(?:\*\*)?("
    + "|".join(HEADINGS)
    + r")\b\s*(?:\*\*)?\s*[:\uff1a]?\s*(?:\*\*)?\s*",
    re.I | re.M,
)


def sections(text: str) -> dict[str, str]:
    markers = list(HEADING.finditer(text))
    out = {}
    for i, marker in enumerate(markers):
        label = next(h for h in HEADINGS if h.casefold() == marker[1].casefold())
        stop = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        content = text[marker.end() : stop].strip()
        if content:
            out[label] = content[:2000]
    return out


def active(prefix: list[ev.Event]) -> bool:
    for event in reversed(prefix):
        if event.type == "user_message" and event.actor is Actor.USER:
            break
        if event.type == "first_pass_started":
            return True
    return False


def valid_followup(events: list[ev.Event], item: ev.TurnReference) -> bool:
    for index, event in enumerate(events):
        if event.id != item.id:
            continue
        prefix = events[:index]
        if not active(prefix):
            return False
        if item.kind == "activity":
            return event.type == "clarification_requested"
        payload = event.parsed()
        if item.kind == "answer" and isinstance(payload, ev.AssistantMessage):
            text = render(payload.message.text(), Evidence.from_events(prefix))
            parts = sections(text)
            return bool(item.excerpt) and item.excerpt in (
                parts.get("Disagreement"),
                parts.get("Next step"),
            )
    return False
