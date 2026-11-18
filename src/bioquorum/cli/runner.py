"""Wiring for `bq run` and `bq review`: the composition root for the agent exit."""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

from bioquorum.agent import Hooks, run_turn
from bioquorum.cli import workspace as wsp
from bioquorum.core import events as ev
from bioquorum.core.enums import PermissionClass, PermissionScope
from bioquorum.core.messages import ToolCallPart, ToolResultPart
from bioquorum.core.refs import QuoteRef, ValueRef
from bioquorum.core.status import VerificationStatus
from bioquorum.mcp.client import McpConnection
from bioquorum.models import CassetteMode, ModelCassette, ModelClient, Router
from bioquorum.policy.permissions import Decision, PermissionPolicy, PermissionRequest
from bioquorum.review import ReviewResult, review_answer
from bioquorum.review.binding import find_bindings, render
from bioquorum.review.evidence import Evidence
from bioquorum.storage.events import EventStore
from bioquorum.tools import ToolRegistry, default_registry
from bioquorum.tools.external import register_external

STATUS_STYLE = {
    VerificationStatus.GROUNDED: "cyan",
    VerificationStatus.REVIEWED: "green",
    VerificationStatus.REPRODUCED: "bold green",
    VerificationStatus.UNVERIFIED: "yellow",
    VerificationStatus.DISPUTED: "bold red",
}


def model_client(ws: wsp.Workspace, store: EventStore) -> ModelClient:
    """``BQ_MODEL_CASSETTE`` (+ ``BQ_MODEL_CASSETTE_MODE`` = replay|record) for offline demos/tests."""
    cassette = None
    path = os.environ.get("BQ_MODEL_CASSETTE")
    if path:
        cassette = ModelCassette(
            path, CassetteMode(os.environ.get("BQ_MODEL_CASSETTE_MODE", "replay"))
        )
    return ModelClient(Router(ws.models_config()), store, cassette=cassette)


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
        if call.name == "python_execute":
            code = str(call.args.get("code", "")).strip()
            first = code.splitlines()[0] if code else ""
            more = f"  (+{len(code.splitlines()) - 1} lines)" if "\n" in code else ""
            self.console.print(f"[cyan]> python[/] {first[:110]}{more}")
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


def _unresolved(b) -> str:
    return f"**[unresolved {b.raw}]**"


def answer_evidence(store: EventStore, session_id: str, answer_event_id: str) -> Evidence:
    """What the answer's bindings resolve against: the session up to the answer."""
    events = store.read(session_id)
    seq = next(e.seq for e in events if e.id == answer_event_id)
    return Evidence.from_events([e for e in events if e.seq < seq])


def print_answer(console: Console, text: str, evidence: Evidence) -> None:
    """The answer as the reader should see it (ADR-014): recorded values filled in, quotes
    spelled out, failed bindings marked; then where each binding points."""
    console.print(Markdown(render(text, evidence, failed=_unresolved) or "_(no text)_"))
    lines: list[str] = []
    for b in find_bindings(text, evidence):
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


async def connect_external(
    stack: AsyncExitStack, registry: ToolRegistry, ws: wsp.Workspace, err: Console
):
    servers = ws.mcp_servers()
    for cfg in servers:
        try:
            conn = await stack.enter_async_context(McpConnection(cfg))
            await register_external(registry, conn)
        except Exception as exc:
            err.print(f"[yellow]mcp server {cfg.name} unavailable: {exc}[/]")
    return servers


async def run_question(
    ws: wsp.Workspace,
    question: str,
    *,
    console: Console,
    err: Console,
    session_id: str | None,
    yes: bool,
    review: bool,
    max_steps: int,
) -> int:
    store = EventStore(ws.database())
    options = ws.agent_options()
    sid = session_id or wsp.start_session(ws, store, question[:80])
    client = model_client(ws, store)
    registry = default_registry()
    async with AsyncExitStack() as stack:
        servers = await connect_external(stack, registry, ws, err)
        ctx = wsp.tool_context(
            ws, store, sid, permissions(ws, yes=yes, console=console, servers=servers)
        )
        printer = Printer(console)
        try:
            turn = await run_turn(
                question,
                client=client,
                registry=registry,
                ctx=ctx,
                max_steps=max_steps,
                hooks=Hooks(on_tool_call=printer.tool_call, on_tool_result=printer.tool_result),
                binding=options.binding,
                binding_retries=options.binding_retries,
            )
        finally:
            assert ctx.kernels is not None
            await ctx.kernels.shutdown_all()
    console.rule("answer")
    evidence = answer_evidence(store, sid, turn.answer_event_id)
    print_answer(console, turn.answer.text(), evidence)
    retried = f" · {turn.binding_retries} binding retries" if turn.binding_retries else ""
    console.print(
        f"[dim]session {sid} · {turn.steps} steps · {turn.tool_calls} tool calls{retried} · {', '.join(dict.fromkeys(turn.model_specs))}[/]"
    )
    if turn.stopped_early:
        console.print("[yellow]stopped at the step limit[/]")
    if review:
        console.rule("review")
        result = await review_answer(
            store,
            client,
            session_id=sid,
            classification=ws.classification,
            answer_event_id=turn.answer_event_id,
        )
        print_review(console, result)
    return 0


async def review_session(
    ws: wsp.Workspace,
    *,
    console: Console,
    session_id: str,
    answer_file: Path | None,
    model_checks: bool,
) -> ReviewResult:
    from bioquorum.core.enums import Actor
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
    return await review_answer(
        store,
        client,
        session_id=session_id,
        classification=ws.classification,
        answer_event_id=target,
        use_model_checks=model_checks,
    )
