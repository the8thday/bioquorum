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

Every call ID ends in exactly one ``tool_call_finished`` or ``tool_call_failed`` event,
refusals included, so the conversation rebuilt from events never has a tool call without
a result (providers reject such histories).
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any

from pydantic import ValidationError

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Trust
from bioquorum.core.ids import new_id
from bioquorum.core.messages import DataRefPart, TextPart, ToolResultPart
from bioquorum.policy import classification
from bioquorum.policy.permissions import PermissionRequest
from bioquorum.policy.secrets import display_path, recorded_args, redact
from bioquorum.tools.context import ToolContext
from bioquorum.tools.registry import ToolRegistry
from bioquorum.tools.spec import ToolSpec

_MAX_ERROR = 2000


def _refusal(call_id: str, text: str) -> ToolResultPart:
    # Our own message, not tool output: trusted.
    return ToolResultPart(
        call_id=call_id, content=[TextPart(text=text)], trust=Trust.TRUSTED, is_error=True
    )


def _refuse(
    ctx: ToolContext, call_id: str, tool: str, text: str, causation_id: str | None
) -> ToolResultPart:
    ctx.store.append(
        ev.ToolCallFailed(call_id=call_id, tool=tool, error=redact(text)[:_MAX_ERROR]),
        session_id=ctx.session_id,
        actor=Actor.SYSTEM,
        causation_id=causation_id,
    )
    return _refusal(call_id, text)


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
        err = store.append(
            ev.ErrorOccurred(
                where=f"tools.invoke:{spec.name}", error=str(exc), policy_violation=True
            ),
            session_id=sid,
            actor=Actor.SYSTEM,
            causation_id=causation_id,
        )
        return _refuse(
            ctx, call_id, spec.name, f"Refused by data classification policy: {exc}", err.id
        )

    # 2. arguments
    try:
        parsed = spec.parse_args(args)
    except ValidationError as exc:
        msg = f"invalid arguments: {exc.errors(include_url=False, include_input=False, include_context=False)}"
        store.append(
            ev.ToolCallFailed(call_id=call_id, tool=spec.name, error=redact(msg)[:_MAX_ERROR]),
            session_id=sid,
            actor=Actor.TOOL,
            causation_id=causation_id,
        )
        return _refusal(call_id, msg)

    # 3. permission
    try:
        target = spec.target_of(parsed, ctx)
    except (ValueError, OSError, RuntimeError):
        return _refuse(
            ctx, call_id, spec.name, "The tool target could not be resolved", causation_id
        )
    req = PermissionRequest(
        permission=spec.permission, target=target, session_id=sid, project_id=ctx.project_id
    )
    recorded_target = (
        display_path(target, ctx.workdir)
        if spec.name in ("read_file", "list_files")
        else redact(target)
    )
    decision = ctx.permissions.lookup(req)
    if decision is None:
        request_id = new_id()
        asked = store.append(
            ev.PermissionRequested(
                request_id=request_id,
                permission=spec.permission,
                target=recorded_target,
                reason=spec.name,
            ),
            session_id=sid,
            actor=Actor.SYSTEM,
            causation_id=causation_id,
        )
        try:
            decision = await ctx.permissions.decide(dataclasses.replace(req, request_id=request_id))
        except asyncio.CancelledError:
            store.append(
                ev.PermissionDecided(
                    request_id=request_id,
                    permission=spec.permission,
                    target=recorded_target,
                    granted=False,
                ),
                session_id=sid,
                actor=Actor.SYSTEM,
                causation_id=asked.id,
            )
            raise
        store.append(
            ev.PermissionDecided(
                request_id=request_id,
                permission=spec.permission,
                target=recorded_target,
                granted=decision.granted,
                scope=decision.scope,
            ),
            session_id=sid,
            actor=Actor.USER if decision.asked else Actor.SYSTEM,
            causation_id=asked.id,
        )
    if not decision.granted:
        return _refuse(
            ctx,
            call_id,
            spec.name,
            f"Permission '{spec.permission}' denied for {recorded_target}",
            causation_id,
        )

    # 4. requested
    requested = store.append(
        ev.ToolCallRequested(
            call_id=call_id,
            tool=spec.name,
            args=recorded_args(spec.name, args, ctx.workdir),
            permission=spec.permission,
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
    except asyncio.CancelledError:
        _refuse(ctx, call_id, spec.name, "Research stopped", requested.id)
        raise
    except Exception as exc:  # tool bugs must not crash the agent loop
        error = redact(
            type(exc).__name__
            if spec.name in ("read_file", "list_files")
            else f"{type(exc).__name__}: {exc}"
        )[:_MAX_ERROR]
        store.append(
            ev.ToolCallFailed(call_id=call_id, tool=spec.name, error=error),
            session_id=sid,
            actor=Actor.TOOL,
            causation_id=requested.id,
        )
        return _refusal(call_id, f"Tool '{spec.name}' failed: {error}")

    # 6-8. wrap, finish (audit is the projection of tool_call_finished)
    content = []
    for item in result.content:
        if isinstance(item, TextPart):
            item = item.model_copy(update={"text": redact(item.text)})
        elif isinstance(item, DataRefPart):
            item = item.model_copy(update={"summary": redact(item.summary)})
        content.append(item)
    part = ToolResultPart(
        call_id=call_id, content=content, trust=spec.output_trust, is_error=result.is_error
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
    registry: ToolRegistry,
    name: str,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    skip_reason: str | None = None,
    **kw: Any,
) -> ToolResultPart:
    """Convenience for callers holding a registry. Unknown tools are refused, not raised."""
    if skip_reason is not None or name not in registry:
        call_id = kw.get("call_id") or new_id()
        reason = f"Tool skipped: {skip_reason}" if skip_reason else f"Unknown tool '{name}'"
        return _refuse(ctx, call_id, name, reason, kw.get("causation_id"))
    return await invoke(registry.get(name), args, ctx, **kw)
