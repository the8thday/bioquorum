"""The model's context is a projection of the event log (ARCHITECTURE.md §5.4, §6.6).

There is no second conversation history: user and assistant messages and tool results
are read back from events every step, so the loop is stateless and can resume after a crash.
"""

from __future__ import annotations

from collections.abc import Iterable

from bioquorum.core import events as ev
from bioquorum.core.enums import Trust
from bioquorum.core.messages import Message, TextPart, ToolResultPart
from bioquorum.review.evidence import source_aliases


def conversation(events: Iterable[ev.Event], *, source_labels: bool = False) -> list[Message]:
    """``source_labels``: start each quotable tool result with ``[source sN]``, the alias that
    quote markers use (ADR-014). Aliases come from the same rule the review uses."""
    items = list(events)
    aliases = source_aliases(items) if source_labels else {}
    out: list[Message] = []
    for e in items:
        p = e.parsed()
        if isinstance(p, ev.UserMessage | ev.AssistantMessage):
            out.append(p.message)
        elif isinstance(p, ev.ToolCallFinished):
            result = p.result
            alias = aliases.get(p.call_id)
            if alias is not None:
                label = TextPart(text=f"[source {alias}]")
                result = result.model_copy(update={"content": [label, *result.content]})
            out.append(Message.tool(result))
        elif isinstance(p, ev.ToolCallFailed):
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
