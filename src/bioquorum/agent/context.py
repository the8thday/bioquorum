"""The model's context is a projection of the event log (ARCHITECTURE.md §5.4, §6.6).

There is no second conversation history: user and assistant messages and tool results
are read back from events every step, so the loop is stateless and can resume after a crash.
"""

from __future__ import annotations

from collections.abc import Iterable

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Trust
from bioquorum.core.messages import Message, TextPart, ToolResultPart, fence_untrusted
from bioquorum.review.evidence import source_aliases


def conversation(
    events: Iterable[ev.Event],
    *,
    source_labels: bool = False,
    compacted: bool = False,
    through_seq: int | None = None,
) -> list[Message]:
    """``source_labels``: start each quotable tool result with ``[source sN]``, the alias that
    quote markers use (ADR-014). Aliases come from the same rule the review uses."""
    items = list(events)
    aliases = source_aliases(items) if source_labels else {}
    delegated = {
        report.parent_call_id: report.sources
        for event in items
        if event.type == ev.LiteratureFinished.event_type
        and (through_seq is None or event.seq <= through_seq)
        and isinstance(report := event.parsed(), ev.LiteratureFinished)
        and report.outcome == "completed"
    }
    out: list[Message] = []
    summary = next(
        (
            p
            for e in reversed(items)
            if compacted
            and e.type == ev.ContextCompacted.event_type
            and isinstance(p := e.parsed(), ev.ContextCompacted)
            and (through_seq is None or p.to_seq <= through_seq)
        ),
        None,
    )
    latest_user = next(
        (
            e.seq
            for e in reversed(items)
            if e.type == ev.UserMessage.event_type and e.actor is Actor.USER
        ),
        None,
    )
    if summary:
        out.append(
            Message.user("Earlier conversation summary:\n" + fence_untrusted(summary.summary))
        )

    def excerpt_text(q: ev.LiteratureExcerpt) -> str:
        marker = f"[source {aliases[q.call_id]}]\n" if q.call_id in aliases else "Source excerpt:\n"
        return marker + (f"{q.identifier}\n" if q.identifier else "") + q.text

    pending: set[str] = set()
    for e in items:
        if through_seq is not None and e.seq > through_seq:
            break
        if summary and summary.from_seq <= e.seq <= summary.to_seq and e.seq != latest_user:
            continue
        p = e.parsed()
        if isinstance(p, ev.UserMessage | ev.AssistantMessage):
            out.append(p.message)
            pending.update(c.call_id for c in p.message.tool_calls())
        elif isinstance(p, ev.LiteratureFinished) and p.prefetched:
            excerpts = "\n\n".join(excerpt_text(q) for q in p.sources)
            detail = (
                "Helper synthesis (unverified):\n" + p.summary + "\n\n" + excerpts
                if p.outcome == "completed"
                else f"Lookup {p.outcome}: {p.reason}"
            )
            out.append(
                Message.user(
                    "BioQuorum's explicit literature lookup for the preceding question:\n"
                    + fence_untrusted(detail)
                )
            )
        elif isinstance(p, ev.ToolCallFinished) and p.call_id in pending:
            pending.remove(p.call_id)
            result = p.result
            alias = aliases.get(p.call_id)
            if alias is not None:
                label = TextPart(text=f"[source {alias}]")
                result = result.model_copy(update={"content": [label, *result.content]})
            if excerpts := delegated.get(p.call_id):
                quoted = [TextPart(text=excerpt_text(q)) for q in excerpts]
                result = result.model_copy(update={"content": [*result.content, *quoted]})
            out.append(Message.tool(result))
        elif isinstance(p, ev.ToolCallFailed) and p.call_id in pending:
            pending.remove(p.call_id)
            out.append(
                Message.tool(
                    ToolResultPart(
                        call_id=p.call_id,
                        content=[TextPart(text=p.error)],
                        trust=Trust.TRUSTED,  # written by BioQuorum, not by the tool
                        is_error=True,
                    )
                )
            )
    return out


def last_assistant(events: Iterable[ev.Event]) -> ev.Event | None:
    found = None
    for e in events:
        if e.type == ev.AssistantMessage.event_type:
            found = e
    return found
