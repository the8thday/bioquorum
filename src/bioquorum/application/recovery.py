"""Reconstruct the last local turn and its durable execution boundary."""

from dataclasses import dataclass

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.review_record import answer_reviews


@dataclass
class Checkpoint:
    request: ev.Event
    plan: ev.PlanProposed | None
    events: list[ev.Event]
    answer_id: str | None
    reviewed: bool
    uncertain: dict[str, str]


def checkpoint(events: list[ev.Event]) -> Checkpoint | None:
    start = next((e for e in reversed(events) if e.type == "turn_requested"), None)
    if not start:
        return None
    tail = [e for e in events if e.seq > start.seq]
    state = None
    plan = None
    approved = False
    answer = None
    called = {}
    uncertain = {}
    for e in tail:
        p = e.parsed()
        if isinstance(p, ev.PlanProposed):
            plan = p
        elif isinstance(p, ev.PlanApproved):
            approved = plan is not None and p.plan_id == plan.plan_id
        elif isinstance(p, ev.TurnStarted | ev.TurnResumed):
            state = "running"
            if isinstance(p, ev.TurnResumed):
                for call_id in p.checked_calls:
                    uncertain.pop(call_id, None)
        elif isinstance(p, ev.TurnFinished):
            state = p.outcome
        elif isinstance(p, ev.UserMessage):
            if state == "interrupted" and e.actor is Actor.USER:
                return None
            answer = None
        elif isinstance(p, ev.AssistantMessage):
            answer = None if p.message.tool_calls() else e.id
        elif isinstance(p, ev.ToolCallRequested):
            called[p.call_id] = p.tool
        elif (
            isinstance(p, ev.ToolCallFailed)
            and p.call_id in called
            and ("previous process exited" in p.error.lower() or p.error == "Research stopped")
        ):
            uncertain[p.call_id] = p.tool
    if state != "interrupted" or (start.payload.get("plan_id") and not approved):
        return None
    review = answer_reviews(tail).get(answer) if answer else None
    reviewed = bool(review and review.review_id)
    return Checkpoint(start, plan, tail, answer, reviewed, uncertain)
