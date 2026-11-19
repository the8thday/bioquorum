import pytest

from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, MessageRole
from bioquorum.core.messages import Message

pytestmark = pytest.mark.anyio


async def test_plan_gate_and_fork(tmp_path):
    controls = ResearchControls(init_project(tmp_path))
    sid = controls.new_session("Original")
    event = controls.submit(sid, "Compare evidence", steps=["Gather sources"])
    assert controls.store.read(sid)[-1].type == "plan_proposed"
    assert not controls.tasks
    await controls.decide_plan(sid, event.id, approve=False, reason="Refine scope")
    assert not controls.tasks
    controls.store.append(
        ev.UserMessage(message=Message.user("Parent")), session_id=sid, actor=Actor.USER
    )
    child = controls.fork(sid, "Alternative")
    controls.store.append(
        ev.UserMessage(message=Message.user("Later")), session_id=sid, actor=Actor.USER
    )
    assert [
        payload.message.text()
        for e in controls.store.history(child)
        if isinstance(payload := e.parsed(), ev.UserMessage)
    ] == ["Parent"]


async def wait_for(predicate):
    import asyncio

    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_interrupt_pending_permission_and_continue(tmp_path):
    from pydantic_ai import messages as pa
    from pydantic_ai.models.function import FunctionModel

    from bioquorum.core.enums import Classification
    from bioquorum.models import ModelClient, ModelsConfig, Router

    cfg = ModelsConfig.from_dict(
        {
            "providers": {"deepseek": {}},
            "roles": {"worker": "deepseek:worker", "reviewer": "deepseek:reviewer"},
        }
    )

    def script(messages, info):
        parts = [p for message in messages for p in message.parts]
        if any(
            "split a research assistant" in p.content
            for p in parts
            if isinstance(p, pa.SystemPromptPart)
        ):
            return pa.ModelResponse(parts=[pa.TextPart(content='{"claims": []}')])
        latest = [p.content for p in parts if isinstance(p, pa.UserPromptPart)][-1]
        if latest == "New direction":
            assert any(isinstance(p, pa.ToolReturnPart) for p in parts)
            return pa.ModelResponse(parts=[pa.TextPart(content="Changed direction.")])
        return pa.ModelResponse(
            parts=[
                pa.ToolCallPart(
                    "download_file", {"url": "https://example.org/test"}, tool_call_id="network"
                ),
                pa.ToolCallPart(
                    "python_execute", {"code": "print('not run')"}, tool_call_id="pending"
                ),
            ]
        )

    ws = init_project(tmp_path, classification=Classification.PUBLIC)
    controls = ResearchControls(
        ws,
        make_client=lambda _, store: ModelClient(
            Router(cfg),
            store,
            factory=lambda _: FunctionModel(script),
        ),
    )
    sid = controls.new_session("Interrupt")
    controls.submit(sid, "Initial")
    await wait_for(lambda: controls.tasks[sid].permissions)
    await controls.stop(sid)
    events = controls.store.read(sid)
    assert events[-1].payload["outcome"] == "interrupted"
    assert [e.type for e in events].count("permission_decided") == 1
    assert [e.type for e in events].count("tool_call_failed") == 2
    controls.submit(sid, "New direction")
    await controls.tasks[sid].task
    assert controls.store.read(sid)[-1].payload["outcome"] == "completed"


async def test_recovery_closes_orphaned_tool_and_keeps_proposed_plan(tmp_path):
    from bioquorum.core.messages import ToolCallPart

    ws = init_project(tmp_path)
    controls = ResearchControls(ws)
    sid = controls.new_session("Interrupted")
    orphan = controls.store.append(
        ev.TurnRequested(question="Count"), session_id=sid, actor=Actor.USER
    )
    controls.store.append(ev.TurnStarted(turn_id=orphan.id), session_id=sid, actor=Actor.SYSTEM)
    controls.store.append(
        ev.AssistantMessage(
            message=Message(
                role=MessageRole.ASSISTANT,
                parts=[ToolCallPart(call_id="orphan", name="python_execute", args={})],
            )
        ),
        session_id=sid,
        actor=Actor.AGENT,
    )
    other = controls.new_session("Pending")
    plan = controls.submit(other, "Compare", steps=["Find evidence"])
    controls.store.append(
        ev.ErrorOccurred(where="other operation", error="unrelated"),
        session_id=other,
        actor=Actor.SYSTEM,
    )
    await controls.recover()
    await controls.recover()
    repaired = controls.store.read(sid)
    assert [e.payload["call_id"] for e in repaired if e.type == "tool_call_failed"] == ["orphan"]
    assert [e.payload["outcome"] for e in repaired if e.type == "turn_finished"] == ["interrupted"]
    pending = controls.pending(controls.store.read(other))
    assert pending and pending[0].id == plan.id


@pytest.mark.parametrize("grant", [True, False])
async def test_deviation_requires_a_separate_one_time_decision(tmp_path, grant):
    from pydantic_ai import messages as pa
    from pydantic_ai.models.function import FunctionModel

    from bioquorum.core.enums import Classification
    from bioquorum.models import ModelClient, ModelsConfig, Router

    cfg = ModelsConfig.from_dict(
        {
            "providers": {"deepseek": {}},
            "roles": {"worker": "deepseek:worker", "reviewer": "deepseek:reviewer"},
        }
    )

    def script(messages, info):
        parts = [p for message in messages for p in message.parts]
        system = next(p.content for p in parts if isinstance(p, pa.SystemPromptPart))
        if "split a research assistant" in system:
            return pa.ModelResponse(parts=[pa.TextPart(content='{"claims": []}')])
        if any(isinstance(p, pa.ToolReturnPart) for p in parts):
            return pa.ModelResponse(parts=[pa.TextPart(content="The result is recorded.")])
        return pa.ModelResponse(
            parts=[
                pa.ToolCallPart(
                    "request_plan_deviation",
                    {
                        "change": "Compare medians instead of means",
                        "reason": "The sample is skewed",
                    },
                    tool_call_id="deviation",
                ),
            ]
        )

    controls = ResearchControls(
        init_project(tmp_path, classification=Classification.PUBLIC),
        make_client=lambda _, store: ModelClient(
            Router(cfg), store, factory=lambda _: FunctionModel(script)
        ),
    )
    sid = controls.new_session("Holdout control")
    turn = controls.submit(sid, "Summarize a skewed measurement", steps=["Estimate the mean"])
    assert not controls.tasks
    await controls.decide_plan(sid, turn.id, approve=True)
    await wait_for(lambda: controls.tasks[sid].permissions)
    events = controls.store.read(sid)
    req = next(p for e in events if isinstance(p := e.parsed(), ev.PermissionRequested))
    assert "The sample is skewed" in req.target
    assert not any(e.type == "plan_deviation" for e in events)
    controls.decide_permission(sid, req.request_id, grant)
    await controls.tasks[sid].task
    events = controls.store.read(sid)
    assert [e.payload["granted"] for e in events if e.type == "permission_decided"] == [grant]
    assert [e.payload["scope"] for e in events if e.type == "permission_decided"] == [
        "once" if grant else None
    ]
    assert len([e for e in events if e.type == "plan_deviation"]) == int(grant)


async def test_stop_running_kernel_closes_run_and_tool(tmp_path):
    from pydantic_ai import messages as pa
    from pydantic_ai.models.function import FunctionModel

    from bioquorum.core.enums import Classification
    from bioquorum.models import ModelClient, ModelsConfig, Router
    from bioquorum.runtime import FakeKernel, Script

    cfg = ModelsConfig.from_dict(
        {
            "providers": {"deepseek": {}},
            "roles": {"worker": "deepseek:worker", "reviewer": "deepseek:reviewer"},
        }
    )
    result = pa.ModelResponse(
        parts=[
            pa.ToolCallPart("python_execute", {"code": "print('waiting')"}, tool_call_id="kernel"),
        ]
    )
    kernel = FakeKernel().queue(Script(hang=True))

    async def factory(_):
        return kernel

    controls = ResearchControls(
        init_project(tmp_path, classification=Classification.PUBLIC),
        make_client=lambda _, store: ModelClient(
            Router(cfg),
            store,
            factory=lambda _: FunctionModel(lambda *_: result),
        ),
        kernel_factory=factory,
    )
    sid = controls.new_session("Stop a calculation")
    controls.submit(sid, "Run a calculation")
    await wait_for(lambda: kernel.executed)
    await controls.stop(sid)
    event_types = [e.type for e in controls.store.read(sid)]
    assert event_types[-3:] == ["run_cancelled", "tool_call_failed", "turn_finished"]
    assert kernel.closed
