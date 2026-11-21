"""Wiring for `bq run` and `bq review`: the composition root for the agent exit."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.status import Status
from rich.table import Table

from bioquorum.agent import Hooks
from bioquorum.application import research
from bioquorum.application import workspace as wsp
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionClass, PermissionScope
from bioquorum.core.messages import ToolCallPart, ToolResultPart
from bioquorum.core.refs import QuoteRef, ValueRef
from bioquorum.core.status import VerificationStatus
from bioquorum.policy.permissions import Decision, PermissionPolicy, PermissionRequest
from bioquorum.review import ReviewResult
from bioquorum.review.binding import ground_claim, render
from bioquorum.review.evidence import Evidence
from bioquorum.storage.events import EventStore

STATUS_STYLE = {
    VerificationStatus.GROUNDED: "cyan",
    VerificationStatus.REVIEWED: "green",
    VerificationStatus.REPRODUCED: "bold green",
    VerificationStatus.REPLICATED: "bold green",
    VerificationStatus.UNVERIFIED: "yellow",
    VerificationStatus.DISPUTED: "bold red",
}


model_client = research.model_client
answer_evidence = research.answer_evidence
citation_context = research.citation_context


def terminal_asker(console: Console):
    async def ask(req: PermissionRequest) -> Decision:
        if not sys.stdin.isatty():
            return Decision(granted=False)
        console.print(f"[bold yellow]permission[/] {req.permission.value} -> {req.target}")
        answer = console.input("  allow? [o]nce / [s]ession / [p]roject / [N]o: ").strip().lower()
        scope = {
            "o": PermissionScope.ONCE,
            "s": PermissionScope.SESSION,
            "p": PermissionScope.PROJECT,
        }.get(answer[:1])
        return Decision(granted=scope is not None, scope=scope)

    return ask


def permissions(ws: wsp.Workspace, *, yes: bool, console: Console, servers) -> PermissionPolicy:
    base = wsp.default_permissions(ws, allow=list(PermissionClass) if yes else [], servers=servers)
    policy = PermissionPolicy(ask=None if yes else terminal_asker(console))
    for g in base.grants():
        policy.grant(
            g.permission, g.target, g.scope, session_id=g.session_id, project_id=g.project_id
        )
    return policy


def _short(args: dict) -> str:
    text = json.dumps(args, ensure_ascii=False)
    return text if len(text) <= 140 else text[:137] + "..."


@dataclass
class Printer:
    console: Console

    def tool_call(self, call: ToolCallPart) -> None:
        if call.name in ("python_execute", "r_execute"):
            code = str(call.args.get("code", "")).strip()
            first = code.splitlines()[0] if code else ""
            more = f"  (+{len(code.splitlines()) - 1} lines)" if "\n" in code else ""
            self.console.print(
                f"[cyan]> {call.name.removesuffix('_execute')}[/] {first[:110]}{more}"
            )
        else:
            self.console.print(f"[cyan]> {call.name}[/] {_short(call.args)}")

    def tool_result(self, call: ToolCallPart, result: ToolResultPart) -> None:
        text = (
            result.text()
            .replace("<untrusted-tool-output>", "")
            .replace("</untrusted-tool-output>", "")
        )
        lines = [ln for ln in text.strip().splitlines() if ln.strip()]
        head = " | ".join(lines[:3])[:160]
        style = "red" if result.is_error else "dim"
        self.console.print(f"  [{style}]{head}{' ...' if len(lines) > 3 else ''}[/]")


@dataclass
class StreamProgress:
    status: Status
    characters: int = 0

    def delta(self, text: str) -> None:
        self.characters += len(text)
        self.status.update(f"Generating · {self.characters:,} text characters received")

    def pause(self) -> None:
        self.status.stop()
        self.characters = 0
        self.status.update("Generating")

    def resume(self) -> None:
        self.status.start()


def print_usage(console: Console, events: list[ev.Event], *, after_seq: int, label: str) -> None:
    calls = [
        p
        for e in events
        if e.seq > after_seq
        and e.type in (ev.ModelCallFinished.event_type, ev.LiteratureFinished.event_type)
        and isinstance(p := e.parsed(), ev.ModelCallFinished | ev.LiteratureFinished)
    ]
    if not calls:
        return
    known = [p for p in calls if p.cost_known]
    cost = f"estimated USD ${sum(p.cost_usd for p in known):.6f}" if known else "price unavailable"
    unpriced = sum(
        p.model_calls - p.priced_calls
        if isinstance(p, ev.LiteratureFinished)
        else int(not p.cost_known)
        for p in calls
    )
    if known and unpriced:
        cost += f" ({unpriced} unpriced)"
    failures = sum(e.seq > after_seq and e.type == ev.ModelCallFailed.event_type for e in events)
    failures += sum(p.failed_calls for p in calls if isinstance(p, ev.LiteratureFinished))
    if failures:
        cost += f" ({failures} failed calls excluded)"
    console.print(
        f"[dim]{label}: {sum(p.input_tokens for p in calls):,} input + "
        f"{sum(p.output_tokens for p in calls):,} output tokens · {cost}[/]"
    )


def _unresolved(b) -> str:
    return f"**[unresolved {b.raw}]**"


def print_answer(console: Console, text: str, evidence: Evidence) -> None:
    """The answer as the reader should see it (ADR-014): recorded values filled in, quotes
    spelled out, failed bindings marked; then where each binding points."""
    console.print(Markdown(render(text, evidence, failed=_unresolved) or "_(no text)_"))
    grounding = ground_claim(text, evidence)
    lines: list[str] = []
    for b in grounding.bindings:
        if isinstance(b.ref, ValueRef):
            nv = evidence.values[b.ref.name].value
            unit = f" {nv.unit}" if nv.unit else ""
            lines.append(
                f"[cyan]{{{{{nv.name}}}}}[/] = {nv.value}{unit}  [dim]run {b.ref.run_id}[/]"
            )
        elif isinstance(b.ref, QuoteRef):
            ref = b.ref
            tool = next((s.tool for s in evidence.sources if s.call_id == ref.call_id), "?")
            lines.append(f"[cyan]{escape(f'[{b.source}]')}[/] {tool}  [dim]call {ref.call_id}[/]")
        else:
            lines.append(f"[red]x[/] {escape(b.raw[:80])}: {escape(b.problem)}")
    if lines:
        console.print("[bold]evidence[/]")
        for line in dict.fromkeys(lines):
            console.print(f"  {line}", markup=True, highlight=False)
    if grounding.unbound:
        console.print("[yellow]Unbound numbers and citations · assertions only[/]")
        console.print("  " + ", ".join(dict.fromkeys(grounding.unbound)), markup=False)


def print_review(console: Console, result: ReviewResult) -> None:
    evidence = result.evidence
    table = Table("#", "status", "claim", "finding", show_lines=False)
    for c in result.claims:
        style = STATUS_STYLE[c.status]
        finding = "; ".join(f"{f.check}: {f.evidence}" for f in c.findings)
        text = render(c.claim.text, evidence) if evidence is not None else c.claim.text
        table.add_row(
            str(c.claim.index),
            f"[{style}]{c.status.value}[/]",
            escape(text[:90]),
            escape(finding[:120]),
        )
    console.print(table)
    overall = result.status
    console.print(
        f"overall: [{STATUS_STYLE[overall]}]{overall.value}[/]  ({len(result.findings)} findings)"
    )
    for e in result.errors:
        console.print(f"[yellow]check not run: {e}[/]")


async def run_question(
    ws: wsp.Workspace,
    question: str,
    *,
    console: Console,
    err: Console,
    session_id: str | None,
    yes: bool,
    review: bool,
    max_steps: int | None,
    stream: bool | None = None,
    online_citations: bool = False,
) -> int:
    try:
        research.literature_request(question)
    except ValueError as exc:
        raise wsp.WorkspaceError(str(exc)) from None
    store = EventStore(ws.database())
    options = ws.agent_options()
    sid = session_id or wsp.start_session(ws, store, question[:80])
    start_seq = max((e.seq for e in store.read(sid)), default=-1)
    client = model_client(ws, store)
    printer = Printer(console)
    streaming = options.stream if stream is None else stream
    progress = StreamProgress(err.status("Generating")) if streaming else None

    def tool_call(call: ToolCallPart) -> None:
        if progress:
            progress.pause()
        printer.tool_call(call)

    def tool_result(call: ToolCallPart, result: ToolResultPart) -> None:
        printer.tool_result(call, result)
        if progress:
            progress.resume()

    try:
        if progress:
            progress.resume()
        turn = await research.execute(
            ws,
            store,
            sid,
            question,
            client=client,
            permissions=lambda servers: permissions(ws, yes=yes, console=console, servers=servers),
            max_steps=max_steps,
            hooks=Hooks(
                on_tool_call=tool_call,
                on_tool_result=tool_result,
                on_model_delta=progress.delta if progress else None,
            ),
            warnings=lambda message: err.print(message, markup=False),
        )
    finally:
        if progress:
            progress.pause()
    console.rule("answer")
    evidence = research.answer_evidence(store, sid, turn.answer_event_id)
    print_answer(console, turn.answer.text(), evidence)
    retried = f" · {turn.binding_retries} binding retries" if turn.binding_retries else ""
    console.print(
        f"[dim]session {sid} · {turn.steps} steps · {turn.tool_calls} tool calls{retried} · {', '.join(dict.fromkeys(turn.model_specs))}[/]"
    )
    if turn.stopped_early:
        console.print(f"[yellow]exploration stopped: {turn.stop_reason}[/]")
    explored = store.read(sid)
    if options.show_cost:
        print_usage(console, explored, after_seq=start_seq, label="Answer usage")
    if review:
        console.rule("review")
        result = await research.audit(
            ws,
            store,
            sid,
            client,
            answer_event_id=turn.answer_event_id,
            online_citations=online_citations,
        )
        print_review(console, result)
        if options.show_cost:
            print_usage(console, store.read(sid), after_seq=explored[-1].seq, label="Review usage")
    return 0


async def review_session(
    ws: wsp.Workspace,
    *,
    console: Console,
    session_id: str,
    answer_file: Path | None,
    model_checks: bool,
    online_citations: bool = False,
) -> ReviewResult:
    from bioquorum.core.messages import Message

    store = EventStore(ws.database())
    target = None
    if answer_file is not None:  # e.g. an answer Claude Code wrote using `bq mcp serve`
        text = answer_file.read_text(encoding="utf-8")
        target = store.append(
            ev.AssistantMessage(message=Message.assistant(text)),
            session_id=session_id,
            actor=Actor.AGENT,
        ).id
    client = model_client(ws, store)
    return await research.audit(
        ws,
        store,
        session_id,
        client,
        answer_event_id=target,
        model_checks=model_checks,
        online_citations=online_citations,
    )
