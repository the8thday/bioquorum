"""Minimal agent loop (ARCHITECTURE.md §6.6).

user_message -> [model call -> assistant_message -> tool calls via invoke()]* -> answer.
Every step is driven by events: the context is rebuilt from the log each turn. The loop
never touches runtime, connectors or MCP directly (ADR-006, enforced by import-linter).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from bioquorum.agent.context import conversation
from bioquorum.agent.prompts import WORKER_SYSTEM
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ModelRole
from bioquorum.core.messages import Message, ToolCallPart, ToolResultPart
from bioquorum.models.client import ModelClient
from bioquorum.tools.context import ToolContext
from bioquorum.tools.invoke import invoke_by_name
from bioquorum.tools.registry import ToolRegistry

DEFAULT_MAX_STEPS = 12

OnToolCall = Callable[[ToolCallPart], Awaitable[None] | None]
OnToolResult = Callable[[ToolCallPart, ToolResultPart], Awaitable[None] | None]


@dataclass
class Hooks:
    """Optional callbacks for a UI (the CLI prints progress). They observe; they decide nothing."""

    on_tool_call: OnToolCall | None = None
    on_tool_result: OnToolResult | None = None


@dataclass
class TurnResult:
    answer: Message
    answer_event_id: str
    steps: int
    tool_calls: int = 0
    stopped_early: bool = False
    model_specs: list[str] = field(default_factory=list)


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
    max_steps: int = DEFAULT_MAX_STEPS,
    hooks: Hooks | None = None,
    system: str = WORKER_SYSTEM,
) -> TurnResult:
    hooks = hooks or Hooks()
    store, sid = ctx.store, ctx.session_id
    asked = store.append(
        ev.UserMessage(message=Message.user(question)), session_id=sid, actor=Actor.USER
    )
    tools = registry.definitions()
    cause = asked.id
    calls = 0
    specs: list[str] = []
    for step in range(1, max_steps + 1):
        history = [Message.system(system), *conversation(store.read(sid))]
        completion = await client.complete(
            ModelRole.WORKER,
            history,
            tools,
            session_id=sid,
            classification=ctx.classification,
            causation_id=cause,
        )
        specs.append(str(completion.route.spec))
        said = store.append(
            ev.AssistantMessage(message=completion.message, model_call_id=completion.model_call_id),
            session_id=sid,
            actor=Actor.AGENT,
            causation_id=cause,
        )
        tool_calls = completion.message.tool_calls()
        if not tool_calls:
            return TurnResult(completion.message, said.id, step, calls, False, specs)
        for call in tool_calls:
            calls += 1
            await _call(hooks.on_tool_call, call)
            result = await invoke_by_name(
                registry, call.name, call.args, ctx, call_id=call.call_id, causation_id=said.id
            )
            await _call(hooks.on_tool_result, call, result)
        cause = said.id

    # Out of steps: ask once more without tools so the user still gets an answer.
    history = [
        Message.system(system),
        *conversation(store.read(sid)),
        Message.user("Step limit reached. Answer now with what you have; say what is missing."),
    ]
    completion = await client.complete(
        ModelRole.WORKER,
        history,
        (),
        session_id=sid,
        classification=ctx.classification,
        causation_id=cause,
    )
    said = store.append(
        ev.AssistantMessage(message=completion.message, model_call_id=completion.model_call_id),
        session_id=sid,
        actor=Actor.AGENT,
        causation_id=cause,
    )
    return TurnResult(
        completion.message, said.id, max_steps, calls, True, [*specs, str(completion.route.spec)]
    )
