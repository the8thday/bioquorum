"""The model's context is a projection of the event log (ARCHITECTURE.md §5.4, §6.6).

There is no second conversation history: user and assistant messages and tool results
are read back from events every step, so the loop is stateless and can resume after a crash.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import replace

from bioquorum.core import events as ev
from bioquorum.core.activity import ACTIVITY
from bioquorum.core.enums import Actor, Trust
from bioquorum.core.messages import Message, TextPart, ToolResultPart, fence_untrusted
from bioquorum.provenance.catalog import catalog_records
from bioquorum.review.binding import quote_matches, render
from bioquorum.review.evidence import Evidence, result_text, source_aliases


def conversation(
    events: Iterable[ev.Event],
    *,
    source_labels: bool = False,
    references: bool = False,
    load_event: Callable[[str], ev.Event | None] | None = None,
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
            if e.type == ev.ContextCompacted.event_type
            and isinstance(p := e.parsed(), ev.ContextCompacted)
            and (compacted or p.manual)
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
    requests = {
        e.id: e
        for e in items
        if e.type == "turn_requested"
        and (e.payload.get("attachments") or (references and e.payload.get("references")))
    }
    selected: ev.Event | None = None
    for index, e in enumerate(items):
        if e.type == "turn_started":
            selected = requests.get(e.payload["turn_id"])
        if e.type == "turn_finished":
            selected = None
        if through_seq is not None and e.seq > through_seq:
            break
        if summary and summary.from_seq <= e.seq <= summary.to_seq and e.seq != latest_user:
            continue
        p = e.parsed()
        if isinstance(p, ev.UserMessage | ev.AssistantMessage):
            if selected and isinstance(p, ev.UserMessage) and e.actor is Actor.USER:
                turn = selected.parsed()
                assert isinstance(turn, ev.TurnRequested)
                blocks = [
                    reference_text(items[:index], item, load_event=load_event)
                    for item in (turn.references if references else [])
                ]
                text = p.message.text()
                if blocks:
                    text += "\n\nSelected records:\n" + "\n".join(blocks)
                if turn.attachments:
                    files = [
                        q
                        for item in items[:index]
                        if item.type == ev.FileRegistered.event_type
                        and isinstance(q := item.parsed(), ev.FileRegistered)
                        and q.file_id in turn.attachments
                    ]
                    metadata = [
                        {"name": q.filename, "path": q.path, "sha256": q.ref.content_hash}
                        for q in files
                    ]
                    text += (
                        "\n\nAttached project files. When reading one with python_execute, declare its path in input_files.\n"
                        + fence_untrusted(json.dumps(metadata, ensure_ascii=False))
                    )
                out.append(Message.user(text))
                selected = None
            else:
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


def estimated_tokens(messages: list[Message]) -> int:
    """Approximate serialized conversation length at four UTF-8 bytes per token."""
    return (sum(len(m.model_dump_json().encode()) for m in messages) + 3) // 4


def reference_text(
    events: list[ev.Event],
    reference: ev.TurnReference,
    *,
    load_event: Callable[[str], ev.Event | None] | None = None,
) -> str:
    """Resolve the selection inside this conversation's immutable ancestry."""
    kind, ident = reference.kind, reference.id
    data: dict = {"kind": kind, "id": ident}
    source_text = ""
    verified_excerpt = False
    if kind == "artifact" and reference.selection is not None:
        catalog = catalog_records((event, event.parsed()) for event in events)
        product = next((row for row in catalog if row["id"] == ident), None)
        if not product or product["preview"] != reference.selection.type:
            raise ValueError("Referenced artifact is unavailable")
        data.update(
            file=product["name"],
            sha256=product["hash"],
            run=product.get("run"),
            version=product["version"],
            selection=reference.selection.model_dump(),
            path=product.get("path"),
            selected_content=reference.excerpt,
        )
        if run_id := product.get("run"):
            run = next(
                (
                    p
                    for event in events
                    if event.type == "run_started"
                    and isinstance(p := event.parsed(), ev.RunStarted)
                    and p.run_id == run_id
                ),
                None,
            )
            if run:
                data.update(
                    code=run.code[:12000],
                    code_hash=run.code_hash,
                    input_hashes=[item.content_hash for item in run.inputs],
                )
        return fence_untrusted(json.dumps(data, ensure_ascii=False))
    if kind == "source":
        source = next((s for s in Evidence.from_events(events).sources if s.call_id == ident), None)
        if source is None:
            raise ValueError("Referenced source is unavailable")
        data.update(tool=source.tool, alias=source.alias)
        source_text = source.text
        if load_event and source.source_event_id and source.task_id:
            original = load_event(source.source_event_id)
            if (
                original
                and original.session_id == source.task_id
                and isinstance(result := original.parsed(), ev.ToolCallFinished)
                and result.call_id == ident
                and result.tool == source.tool
                and not result.result.is_error
            ):
                source_text = result_text(result.result)
        if reference.excerpt:
            verified_excerpt = quote_matches(replace(source, text=source_text), reference.excerpt)
    elif kind in {"claim", "answer", "activity"}:
        for index, event in enumerate(events):
            payload = event.parsed()
            if (
                kind == "claim"
                and isinstance(payload, ev.ClaimExtracted)
                and payload.claim_id == ident
            ):
                source_text = payload.text
                data["answer"] = payload.source_event_id
                break
            if event.id != ident:
                continue
            if (
                kind == "answer"
                and isinstance(payload, ev.AssistantMessage)
                and not payload.message.tool_calls()
            ):
                source_text = render(payload.message.text(), Evidence.from_events(events[:index]))
                break
            if kind == "activity" and event.type in ACTIVITY:
                data.update(
                    event=event.type,
                    fields={
                        k: event.payload[k] for k in ACTIVITY[event.type] if k in event.payload
                    },
                )
                break
        else:
            raise ValueError("Referenced record is unavailable")
    else:
        run_id = ident
        if kind == "figure":
            output = next(
                (
                    p
                    for e in events
                    if isinstance(p := e.parsed(), ev.RunOutput)
                    and p.ref
                    and p.ref.content_hash == ident
                    and p.run_id == reference.name
                ),
                None,
            )
            if not isinstance(output, ev.RunOutput):
                raise ValueError("Referenced figure is unavailable")
            run_id = output.run_id
        start = next(
            (
                p
                for e in events
                if isinstance(p := e.parsed(), ev.RunStarted) and p.run_id == run_id
            ),
            None,
        )
        if not isinstance(start, ev.RunStarted):
            raise ValueError("Referenced calculation is unavailable")
        data.update(
            run=run_id,
            code=start.code[:12000],
            code_hash=start.code_hash,
            input_hashes=[r.content_hash for r in start.inputs],
        )
        if kind == "value":
            value = next(
                (
                    v
                    for e in events
                    if isinstance(p := e.parsed(), ev.RunFinished) and p.run_id == run_id
                    for v in p.values
                    if v.name == reference.name
                ),
                None,
            )
            if value is None:
                raise ValueError("Referenced value is unavailable")
            data["declaration"] = value.model_dump(mode="json")
    if reference.excerpt:
        if not verified_excerpt and reference.excerpt not in source_text:
            raise ValueError("Selected text does not belong to the referenced record")
        source_text = reference.excerpt
    if source_text:
        data["text"] = source_text[:2000]
    return fence_untrusted(json.dumps(data, ensure_ascii=False))
