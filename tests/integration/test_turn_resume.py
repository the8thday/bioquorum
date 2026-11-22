"""Recover a durable tool result after the research process is killed."""

import asyncio
import multiprocessing
from pathlib import Path

import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.application.controls import ConflictError, ResearchControls
from bioquorum.application.workspace import init_project, load
from bioquorum.core.enums import Classification
from bioquorum.models import ModelClient, ModelsConfig, Router


def controls_for(ws, ready=None):
    cfg = ModelsConfig.from_dict(
        {
            "providers": {"openai": {}},
            "roles": {"worker": "openai:fixture", "reviewer": "openai:review"},
        }
    )

    async def respond(messages, info):
        parts = [part for message in messages for part in message.parts]
        if any(
            isinstance(p, pa.SystemPromptPart) and "split a research assistant" in p.content
            for p in parts
        ):
            return pa.ModelResponse(parts=[pa.TextPart(content='{"claims": []}')])
        returns = [p for p in parts if isinstance(p, pa.ToolReturnPart)]
        if not returns:
            return pa.ModelResponse(parts=[pa.ToolCallPart("record_plan_step", {"step": 1})])
        if ready is not None:
            ready.set()
            await asyncio.Event().wait()
        return pa.ModelResponse(parts=[pa.TextPart(content="The planned step was recorded.")])

    return ResearchControls(
        ws,
        make_client=lambda _, store: ModelClient(
            Router(cfg), store, factory=lambda _: FunctionModel(respond)
        ),
    )


def run_until_killed(root, ready):
    async def run():
        controls = controls_for(
            init_project(Path(root), classification=Classification.PUBLIC), ready
        )
        sid = controls.new_session("Survive restart")
        turn = controls.submit(sid, "Record progress", steps=["Review the input"])
        await controls.decide_plan(sid, turn.id, approve=True)
        await controls.tasks[sid].task

    asyncio.run(run())


@pytest.mark.integration
@pytest.mark.anyio
async def test_resume_after_process_kill(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    worker = ctx.Process(target=run_until_killed, args=(str(tmp_path), ready))
    worker.start()
    try:
        assert await asyncio.to_thread(ready.wait, 20)
    finally:
        worker.kill()
        worker.join(10)
    assert worker.exitcode and worker.exitcode < 0
    controls = controls_for(load(tmp_path))
    sid = controls.sessions()[0]["id"]
    await controls.recover()
    turn = controls.store.read(sid, types=["turn_requested"])[0]
    controls.resume(sid, turn.id)
    with pytest.raises(ConflictError):
        controls.resume(sid, turn.id)
    await controls.tasks[sid].task
    events = controls.store.read(sid)
    assert events[-1].payload["outcome"] == "completed"
    assert [e.type for e in events].count("tool_call_requested") == 1
    assert [e.type for e in events].count("user_message") == 1
    assert [e.type for e in events].count("turn_resumed") == 1
    with pytest.raises(ConflictError):
        controls.resume(sid, turn.id)
