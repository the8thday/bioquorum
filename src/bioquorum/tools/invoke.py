"""``invoke()``: the only way to run a tool (ARCHITECTURE.md §6.4, ADR-006).

Fixed order:
  1. classification check   -> reject + ``error`` event (policy_violation) on failure
  2. argument validation    -> ``tool_call_failed`` on failure
  3. permission check       -> ``permission_requested``/``permission_decided`` when asked
  4. ``tool_call_requested``
  5. execute handler
  6. audit                  -> ``tool_call_finished``/``tool_call_failed`` project into audit_entries
  7. wrap as ToolResultPart(trust=spec.output_trust)
  8. ``tool_call_finished`` or ``tool_call_failed``

invoke never raises for tool-level problems: the caller (agent loop or MCP server) always
gets a ToolResultPart, with ``is_error=True`` when something was refused or failed.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from pydantic import ValidationError

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Trust
from bioquorum.core.ids import new_id
from bioquorum.core.messages import TextPart, ToolResultPart
from bioquorum.policy import classification
from bioquorum.policy.permissions import PermissionRequest
from bioquorum.policy.secrets import redact
from bioquorum.tools.context import ToolContext
from bioquorum.tools.registry import ToolRegistry
from bioquorum.tools.spec import ToolSpec

_MAX_ERROR = 2000


def _refusal(call_id: str, text: str) -> ToolResultPart:
    # Our own message, not tool output: trusted.
    return ToolResultPart(
        call_id=call_id, content=[TextPart(text=text)], trust=Trust.TRUSTED, is_error=True
    )


async def invoke(
    spec: ToolSpec,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    call_id: str | None = None,
    causation_id: str | None = None,
) -> ToolResultPart:
    call_id = call_id or new_id()
    store, sid = ctx.store, ctx.session_id

    # 1. classification
    try:
        classification.check(ctx.classification, spec.classification_floor, what=spec.name)
    except classification.PolicyViolation as exc:
        store.append(
            ev.ErrorOccurred(
                where=f"tools.invoke:{spec.name}", error=str(exc), policy_violation=True
            ),
            session_id=sid,
            actor=Actor.SYSTEM,
            causation_id=causation_id,
        )
        return _refusal(call_id, f"Refused by data classification policy: {exc}")

    # 2. arguments
    try:
        parsed = spec.parse_args(args)
    except ValidationError as exc:
        msg = f"invalid arguments: {exc.errors(include_url=False, include_input=False)}"
        store.append(
            ev.ToolCallFailed(call_id=call_id, tool=spec.name, error=redact(msg)[:_MAX_ERROR]),
            session_id=sid,
            actor=Actor.TOOL,
            causation_id=causation_id,
        )
        return _refusal(call_id, msg)

    # 3. permission
    target = spec.target_of(parsed, ctx)
    req = PermissionRequest(
        permission=spec.permission, target=target, session_id=sid, project_id=ctx.project_id
    )
    decision = ctx.permissions.lookup(req)
    if decision is None:
        request_id = new_id()
        asked = store.append(
            ev.PermissionRequested(
                request_id=request_id, permission=spec.permission, target=target, reason=spec.name
            ),
            session_id=sid,
            actor=Actor.SYSTEM,
            causation_id=causation_id,
        )
        decision = await ctx.permissions.decide(req)
        store.append(
            ev.PermissionDecided(
                request_id=request_id,
                permission=spec.permission,
                target=target,
                granted=decision.granted,
                scope=decision.scope,
            ),
            session_id=sid,
            actor=Actor.USER if decision.asked else Actor.SYSTEM,
            causation_id=asked.id,
        )
    if not decision.granted:
        return _refusal(call_id, f"Permission '{spec.permission}' denied for {target}")

    # 4. requested
    requested = store.append(
        ev.ToolCallRequested(
            call_id=call_id, tool=spec.name, args=args, permission=spec.permission
        ),
        session_id=sid,
        actor=ctx.actor,
        causation_id=causation_id,
    )
    call_ctx = dataclasses.replace(ctx, call_id=call_id, requested_event_id=requested.id)

    # 5. execute
    started = time.monotonic()
    try:
        result = await spec.handler(parsed, call_ctx)
    except Exception as exc:  # tool bugs must not crash the agent loop
        error = redact(f"{type(exc).__name__}: {exc}")[:_MAX_ERROR]
        store.append(
            ev.ToolCallFailed(call_id=call_id, tool=spec.name, error=error),
            session_id=sid,
            actor=Actor.TOOL,
            causation_id=requested.id,
        )
        return _refusal(call_id, f"Tool '{spec.name}' failed: {error}")

    # 6-8. wrap, finish (audit is the projection of tool_call_finished)
    part = ToolResultPart(
        call_id=call_id, content=result.content, trust=spec.output_trust, is_error=result.is_error
    )
    store.append(
        ev.ToolCallFinished(
            call_id=call_id,
            tool=spec.name,
            result=part,
            duration_ms=int((time.monotonic() - started) * 1000),
        ),
        session_id=sid,
        actor=Actor.TOOL,
        causation_id=requested.id,
    )
    return part


async def invoke_by_name(
    registry: ToolRegistry, name: str, args: dict[str, Any], ctx: ToolContext, **kw: Any
) -> ToolResultPart:
    """Convenience for callers holding a registry. Unknown tools are refused, not raised."""
    if name not in registry:
        call_id = kw.get("call_id") or new_id()
        return _refusal(call_id, f"Unknown tool '{name}'")
    return await invoke(registry.get(name), args, ctx, **kw)
