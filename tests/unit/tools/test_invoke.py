"""invoke() is the chokepoint (ADR-006): order, refusals, audit, trust."""

import pytest
from pydantic import BaseModel

from bioquorum.core.enums import Classification, PermissionClass, PermissionScope, Trust
from bioquorum.policy import Decision, PermissionPolicy
from bioquorum.storage.rebuild import snapshot
from bioquorum.tools import ToolRegistry, ToolResult, ToolSpec, invoke, invoke_by_name

pytestmark = pytest.mark.anyio


class Args(BaseModel):
    q: str


calls: list[str] = []


async def echo(args, ctx):
    calls.append(args.q)
    assert ctx.call_id and ctx.requested_event_id  # set per call by invoke
    return ToolResult.text(f"echo {args.q}")


async def boom(args, ctx):
    raise RuntimeError("upstream said api_key=hunter2hunter2")


def spec(**kw):
    base = dict(
        name="echo",
        description="d",
        handler=echo,
        args_model=Args,
        permission=PermissionClass.CONNECTOR,
        classification_floor=Classification.INTERNAL,
    )
    return ToolSpec(**{**base, **kw})


def types(store):
    return [e.type for e in store.read("S")]


async def test_happy_path_event_order_and_untrusted_wrap(ctx, store):
    part = await invoke(spec(), {"q": "EGFR"}, ctx, call_id="C1")
    assert part.call_id == "C1" and part.trust is Trust.UNTRUSTED and not part.is_error
    assert part.text() == "echo EGFR"
    assert types(store) == ["tool_call_requested", "tool_call_finished"]
    finished = store.read("S")[-1].parsed()
    assert finished.result == part
    audit = snapshot(ctx.store.db)["audit_entries"]
    assert [a["action"] for a in audit] == ["tool_call"]


async def test_classification_violation_rejected_and_audited(ctx, store):
    ctx.classification = Classification.CONFIDENTIAL
    before = len(calls)
    part = await invoke(spec(), {"q": "x"}, ctx)
    assert part.is_error and "classification" in part.text()
    assert len(calls) == before  # handler never ran
    assert types(store) == ["error", "tool_call_failed"]  # every call id gets a result event
    audit = snapshot(store.db)["audit_entries"]
    assert any(a["policy_violation"] for a in audit)


async def test_invalid_args_fail_before_permission(ctx, store):
    part = await invoke(spec(), {"wrong": 1}, ctx)
    assert part.is_error and "invalid arguments" in part.text()
    assert types(store) == ["tool_call_failed"]


async def test_permission_denied_without_asker(ctx, store):
    ctx.permissions = PermissionPolicy()
    part = await invoke(spec(), {"q": "x"}, ctx)
    assert part.is_error and "denied" in part.text()
    assert types(store) == ["permission_requested", "permission_decided", "tool_call_failed"]
    assert store.read("S")[1].parsed().granted is False


async def test_asked_permission_recorded_then_remembered(ctx, store):
    asked = []

    async def ask(req):
        asked.append(req.target)
        return Decision(granted=True, scope=PermissionScope.SESSION)

    ctx.permissions = PermissionPolicy(ask)
    await invoke(spec(), {"q": "a"}, ctx)
    await invoke(spec(), {"q": "b"}, ctx)
    assert asked == ["echo"]
    assert types(store) == [
        "permission_requested",
        "permission_decided",
        "tool_call_requested",
        "tool_call_finished",
        "tool_call_requested",
        "tool_call_finished",
    ]
    decided = store.read("S")[1]
    assert decided.actor.value == "user" and decided.causation_id == store.read("S")[0].id


async def test_handler_exception_becomes_failed_event_with_secrets_redacted(ctx, store):
    part = await invoke(spec(handler=boom), {"q": "x"}, ctx)
    assert part.is_error
    failed = store.read("S")[-1]
    assert failed.type == "tool_call_failed"
    assert "hunter2hunter2" not in failed.payload["error"]
    assert "hunter2hunter2" not in part.text()
    assert failed.causation_id == store.read("S")[0].id  # caused by tool_call_requested


async def test_unknown_tool_refused(ctx, store):
    part = await invoke_by_name(ToolRegistry(), "nope", {}, ctx, call_id="C9")
    assert part.is_error and types(store) == ["tool_call_failed"]
    assert store.read("S")[0].payload["call_id"] == "C9"
