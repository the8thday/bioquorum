"""Minimal agent loop (ARCHITECTURE.md §6.6).

user_message -> [model call -> assistant_message -> tool calls via invoke()]* -> answer.
Every step is driven by events: the context is rebuilt from the log each turn. The loop
never touches runtime, connectors or MCP directly (ADR-006, enforced by import-linter).

Mechanisms with their own switches (ADR-018):
- ``binding``: the evidence-binding prompt and ``[source sN]`` labels (ADR-014).
- ``binding_retries``: rounds in which unresolved bindings go back to the model (max 2, §6.5).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from bioquorum.agent.budget import Budget, BudgetMeter
from bioquorum.agent.compaction import compact_history
from bioquorum.agent.context import conversation
from bioquorum.agent.prompts import binding_feedback, worker_system
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ModelRole
from bioquorum.core.messages import Message, ToolCallPart, ToolResultPart
from bioquorum.models.client import ModelClient, OnDelta
from bioquorum.models.config import ConfigError
from bioquorum.policy.secrets import recorded_args, recorded_message
from bioquorum.review.binding import unresolved
from bioquorum.review.evidence import Evidence
from bioquorum.tools.context import ToolContext
from bioquorum.tools.invoke import invoke_by_name
from bioquorum.tools.registry import ToolRegistry

DEFAULT_MAX_STEPS = 12
MAX_BINDING_RETRIES = 2  # the per-finding round-trip cap of ARCHITECTURE.md §6.5

OnToolCall = Callable[[ToolCallPart], Awaitable[None] | None]
OnToolResult = Callable[[ToolCallPart, ToolResultPart], Awaitable[None] | None]


@dataclass
class Hooks:
    """Optional callbacks for a UI (the CLI prints progress). They observe; they decide nothing."""

    on_tool_call: OnToolCall | None = None
    on_tool_result: OnToolResult | None = None
    on_model_delta: OnDelta | None = None


@dataclass
class TurnResult:
    answer: Message
    answer_event_id: str
    steps: int
    tool_calls: int = 0
    stopped_early: bool = False
    model_specs: list[str] = field(default_factory=list)
    binding_retries: int = 0
    stop_reason: str | None = None


async def _call(hook, *args) -> None:
    if hook is not None:
        r = hook(*args)
        if r is not None:
            await r


async def run_turn(
    question: str,
    *,
    client: ModelClient,
    registry: ToolRegistry,
    ctx: ToolContext,
    max_steps: int | None = None,
    hooks: Hooks | None = None,
    system: str | None = None,
    binding: bool = True,
    binding_retries: int = 0,
    budget: Budget | None = None,
    budget_meter: BudgetMeter | None = None,
    before_first_model: Callable[[ev.Event], Awaitable[None]] | None = None,
    references: bool = False,
    compact_at_bytes: int | None = None,
) -> TurnResult:
    hooks = hooks or Hooks()
    system = system if system is not None else worker_system(binding=binding)
    meter = budget_meter or BudgetMeter(budget or Budget())
    budget = meter.limits
    if max_steps is None and not budget.enabled:
        max_steps = DEFAULT_MAX_STEPS
    if max_steps is not None and max_steps < 1:
        raise ConfigError("max_steps must be positive")
    if budget.usd is not None:
        roles = [ModelRole.WORKER]
        if compact_at_bytes is not None:
            roles.append(ModelRole.SUMMARIZER)
        for role in roles:
            for route in client.router.chain(role, ctx.classification):
                spec = str(route.spec)
                if spec not in client.prices:
                    raise ConfigError(f"cost budget requires a price for {spec}")
                if client.prices[spec] == (0, 0) and all(
                    limit is None for limit in (max_steps, budget.tokens, budget.seconds)
                ):
                    raise ConfigError(f"cost budget for zero-price {spec} requires another limit")
    retries_left = max(0, min(binding_retries, MAX_BINDING_RETRIES))
    retried = 0
    store, sid = ctx.store, ctx.session_id
    asked = store.append(
        ev.UserMessage(message=recorded_message(Message.user(question))),
        session_id=sid,
        actor=Actor.USER,
    )
    if before_first_model is not None:
        await before_first_model(asked)
    tools = registry.definitions()
    cause = asked.id
    calls = 0
    specs: list[str] = []
    step = 0
    compacted_through = -1
    while (max_steps is None or step < max_steps) and not meter.stop_reason():
        events = store.history(sid)
        if compact_at_bytes is not None:
            compacted_through = await compact_history(
                client,
                ctx,
                events,
                meter,
                target_bytes=compact_at_bytes,
                binding=binding,
                attempted_through=compacted_through,
                references=references,
                cause=cause,
            )
            if meter.stop_reason():
                break
            events = store.history(sid)
        history = [
            Message.system(system),
            *conversation(
                events,
                source_labels=binding,
                compacted=compact_at_bytes is not None,
                references=references,
                load_event=store.get,
            ),
        ]
        step += 1
        completion = await client.complete(
            ModelRole.WORKER,
            history,
            tools,
            session_id=sid,
            classification=ctx.classification,
            causation_id=cause,
            on_delta=hooks.on_model_delta,
        )
        meter.record(completion)
        specs.append(str(completion.route.spec))
        said = store.append(
            ev.AssistantMessage(
                message=recorded_message(completion.message, ctx.workdir),
                model_call_id=completion.model_call_id,
            ),
            session_id=sid,
            actor=Actor.AGENT,
            causation_id=cause,
        )
        tool_calls = completion.message.tool_calls()
        if not tool_calls:
            problems = unresolved(
                completion.message.text(), Evidence.from_events(store.history(sid))
            )
            if (
                problems
                and retries_left > 0
                and not meter.stop_reason()
                and (max_steps is None or step < max_steps)
            ):
                retries_left -= 1
                retried += 1
                feedback = binding_feedback([(b.raw, b.problem) for b in problems])
                cause = store.append(
                    ev.UserMessage(message=Message.user(feedback)),
                    session_id=sid,
                    actor=Actor.SYSTEM,  # written by BioQuorum, not by the user
                    causation_id=said.id,
                ).id
                continue
            return TurnResult(completion.message, said.id, step, calls, False, specs, retried)
        try:
            for call in tool_calls:
                calls += 1
                await _call(
                    hooks.on_tool_call,
                    call.model_copy(
                        update={"args": recorded_args(call.name, call.args, ctx.workdir)}
                    ),
                )
                result = await invoke_by_name(
                    registry,
                    call.name,
                    call.args,
                    ctx,
                    call_id=call.call_id,
                    causation_id=said.id,
                    skip_reason=meter.stop_reason(),
                )
                await _call(hooks.on_tool_result, call, result)
        except asyncio.CancelledError:
            finished = {
                p.call_id
                for e in store.read(sid, from_seq=said.seq + 1)
                if isinstance(p := e.parsed(), ev.ToolCallFinished | ev.ToolCallFailed)
            }
            for call in tool_calls:
                if call.call_id not in finished:
                    await invoke_by_name(
                        registry,
                        call.name,
                        call.args,
                        ctx,
                        call_id=call.call_id,
                        causation_id=said.id,
                        skip_reason="Research stopped",
                    )
            raise
        cause = said.id

    # An exhausted exploration budget still allows a tool-free answer.
    reason = meter.stop_reason() or "step limit"
    cause = store.append(
        ev.ExplorationStopped(reason=reason),
        session_id=sid,
        actor=Actor.SYSTEM,
        causation_id=cause,
    ).id
    history = [
        Message.system(system),
        *conversation(
            store.history(sid),
            source_labels=binding,
            compacted=compact_at_bytes is not None,
            references=references,
            load_event=store.get,
        ),
        Message.user(
            f"{reason.capitalize()} reached. Answer now with what you have; say what is missing."
        ),
    ]
    completion = await client.complete(
        ModelRole.WORKER,
        history,
        (),
        session_id=sid,
        classification=ctx.classification,
        causation_id=cause,
        on_delta=hooks.on_model_delta,
    )
    answer = completion.message
    if answer.tool_calls():
        # No tool calls may escape the final pass without a matching result in the log.
        answer = answer.model_copy(
            update={"parts": [part for part in answer.parts if not isinstance(part, ToolCallPart)]}
        )
    if not answer.text().strip():
        answer = Message.assistant(f"Could not produce a textual answer before the {reason}.")
    said = store.append(
        ev.AssistantMessage(message=answer, model_call_id=completion.model_call_id),
        session_id=sid,
        actor=Actor.AGENT,
        causation_id=cause,
    )
    return TurnResult(
        answer,
        said.id,
        step,
        calls,
        True,
        [*specs, str(completion.route.spec)],
        retried,
        reason,
    )
