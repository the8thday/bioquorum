import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.agent import Hooks, conversation, run_turn
from bioquorum.core.enums import (
    Actor,
    Classification,
    MessageRole,
    PermissionClass,
    PermissionScope,
)
from bioquorum.core.messages import ThinkingPart
from bioquorum.models import ModelClient, ModelsConfig, Router
from bioquorum.policy import PermissionPolicy
from bioquorum.runtime import Script
from bioquorum.runtime.values import value_output
from bioquorum.tools import default_registry

pytestmark = pytest.mark.anyio
CFG = ModelsConfig.from_dict({"providers": {"deepseek": {}}, "roles": {"worker": "deepseek:w"}})


def scripted(*responses):
    """A model that replays responses in order and records what it was shown."""
    seen: list[list[pa.ModelMessage]] = []

    def fn(messages, info):
        seen.append(list(messages))
        return responses[min(len(seen) - 1, len(responses) - 1)]

    return FunctionModel(fn), seen


def tool_call(name, args):
    return pa.ModelResponse(
        parts=[
            pa.ThinkingPart(content="plan", id="reasoning_content"),
            pa.ToolCallPart(name, args, tool_call_id="provider-id"),
        ]
    )


def text(t):
    return pa.ModelResponse(parts=[pa.TextPart(content=t)])


def client(store, model):
    return ModelClient(Router(CFG), store, factory=lambda route: model)


async def test_tool_then_answer(ctx, store, kernel):
    kernel.on("6*7", Script.stdout("42\n"))
    model, seen = scripted(
        tool_call("python_execute", {"code": "print(6*7)"}), text("1. 6*7 is 42.")
    )
    calls, results = [], []
    turn = await run_turn(
        "What is 6*7?",
        client=client(store, model),
        registry=default_registry(),
        ctx=ctx,
        hooks=Hooks(on_tool_call=calls.append, on_tool_result=lambda c, r: results.append(r)),
    )
    assert turn.answer.text() == "1. 6*7 is 42." and turn.steps == 2 and turn.tool_calls == 1
    assert [c.name for c in calls] == ["python_execute"] and "42" in results[0].text()
    types = [e.type for e in store.read("S")]
    assert types[0] == "user_message" and types[-1] == "assistant_message"
    assert types.count("model_call_finished") == 2 and "run_finished" in types
    # second model call saw the tool result, with our call id, and the thinking replayed
    second = seen[1]
    ret = next(
        p
        for m in second
        if isinstance(m, pa.ModelRequest)
        for p in m.parts
        if isinstance(p, pa.ToolReturnPart)
    )
    assert "42" in str(ret.content) and ret.tool_call_id != "provider-id"
    assert any(
        isinstance(p, pa.ThinkingPart)
        for m in second
        if isinstance(m, pa.ModelResponse)
        for p in m.parts
    )


async def test_context_is_rebuilt_from_events(ctx, store, kernel):
    model, _ = scripted(tool_call("python_execute", {"code": "x"}), text("done"))
    await run_turn("q", client=client(store, model), registry=default_registry(), ctx=ctx)
    msgs = conversation(store.read("S"))
    assert [m.role for m in msgs] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert any(isinstance(p, ThinkingPart) for p in msgs[1].parts)


async def test_refused_tool_still_gets_a_result(ctx, store):
    ctx.permissions = PermissionPolicy()  # nothing granted, nobody to ask
    model, seen = scripted(
        tool_call("python_execute", {"code": "x"}), text("I could not run code.")
    )
    turn = await run_turn("q", client=client(store, model), registry=default_registry(), ctx=ctx)
    assert turn.answer.text() == "I could not run code."
    ret = next(
        p
        for m in seen[1]
        if isinstance(m, pa.ModelRequest)
        for p in m.parts
        if isinstance(p, pa.ToolReturnPart)
    )
    assert "denied" in str(ret.content)


async def test_unknown_tool_and_step_limit(ctx, store):
    model, seen = scripted(tool_call("no_such_tool", {}))  # never stops calling tools
    turn = await run_turn(
        "q", client=client(store, model), registry=default_registry(), ctx=ctx, max_steps=3
    )
    assert turn.stopped_early and turn.steps == 3 and len(seen) == 4
    assert [e.type for e in store.read("S")].count("tool_call_failed") == 3


async def test_token_budget_closes_pending_tools_and_answers(ctx, store, kernel):
    from bioquorum.agent.budget import Budget
    from bioquorum.core import events as ev

    model, seen = scripted(tool_call("python_execute", {"code": "x"}), text("partial"))
    turn = await run_turn(
        "q",
        client=client(store, model),
        registry=default_registry(),
        ctx=ctx,
        budget=Budget(tokens=1),
    )
    assert turn.answer.text() == "partial"
    assert turn.stop_reason == "token budget"
    assert len(seen) == 2 and len(returned_texts(seen[1])) == 1
    assert not any(isinstance(e.parsed(), ev.RunStarted) for e in store.read("S"))
    (stop,) = [e.parsed() for e in store.read("S") if e.type == "exploration_stopped"]
    assert isinstance(stop, ev.ExplorationStopped) and stop.reason == turn.stop_reason


async def test_time_and_dollar_budgets(ctx, store, monkeypatch):
    from bioquorum.agent import budget

    now = 0.0

    def respond(messages, info):
        nonlocal now
        now += 2
        return tool_call("missing", {})

    monkeypatch.setattr(budget, "monotonic", lambda: now)
    model = FunctionModel(respond)
    turn = await run_turn(
        "q",
        client=client(store, model),
        registry=default_registry(),
        ctx=ctx,
        budget=budget.Budget(seconds=1),
    )
    assert turn.stop_reason == "time budget"
    assert not turn.answer.tool_calls()
    with pytest.raises(ValueError, match="price"):
        await run_turn(
            "q",
            client=client(store, model),
            registry=default_registry(),
            ctx=ctx,
            budget=budget.Budget(usd=0.01),
        )


async def test_priced_dollar_budget_counts_reported_usage(ctx, store):
    from bioquorum.agent.budget import Budget
    from bioquorum.core import events as ev

    model, _ = scripted(tool_call("missing", {}), text("partial"))
    priced = ModelClient(
        Router(CFG), store, factory=lambda route: model, prices={"deepseek:w": (1.0, 2.0)}
    )
    turn = await run_turn(
        "q",
        client=priced,
        registry=default_registry(),
        ctx=ctx,
        budget=Budget(usd=0.000001),
    )
    assert turn.stop_reason == "cost budget"
    calls = [p for e in store.read("S") if isinstance(p := e.parsed(), ev.ModelCallFinished)]
    assert len(calls) == 2 and all(p.cost_known and p.cost_usd > 0 for p in calls)


async def test_compaction_replays_summary_and_complete_recent_tool_exchange(ctx, store, kernel):
    from bioquorum.core import events as ev
    from bioquorum.core.messages import Message

    old = "old question " * 200
    store.append(ev.UserMessage(message=Message.user(old)), session_id="S", actor=Actor.USER)
    store.append(
        ev.AssistantMessage(message=Message.assistant("old answer " * 200)),
        session_id="S",
        actor=Actor.AGENT,
    )
    model, seen = scripted(
        tool_call("python_execute", {"code": "x"}), text("compressed notes"), text("done")
    )
    await run_turn(
        "current question",
        client=client(store, model),
        registry=default_registry(),
        ctx=ctx,
        compact_at_bytes=500,
    )
    events = store.read("S")
    assert sum(isinstance(e.parsed(), ev.ContextCompacted) for e in events) == 1
    assert old in [m.text() for m in conversation(events)]
    projected = conversation(events, compacted=True)
    assert old not in [m.text() for m in projected]
    assert "current question" in [m.text() for m in projected]
    assert "compressed notes" in projected[0].text()
    assert returned_texts(seen[2]) and "current question" in repr(seen[2])


async def test_session_grant_reused_across_calls(ctx, store, kernel):
    asked = []

    async def ask(req):
        from bioquorum.policy import Decision

        asked.append(req.permission)
        return Decision(granted=True, scope=PermissionScope.SESSION)

    ctx.permissions = PermissionPolicy(ask)
    model, _ = scripted(
        tool_call("python_execute", {"code": "a"}),
        tool_call("python_execute", {"code": "b"}),
        text("ok"),
    )
    await run_turn("q", client=client(store, model), registry=default_registry(), ctx=ctx)
    assert asked == [PermissionClass.EXECUTE]


async def test_classification_blocks_the_worker(ctx, store):
    ctx.classification = Classification.CONFIDENTIAL  # deepseek is remote: not allowed
    from bioquorum.policy import PolicyViolation

    model, _ = scripted(text("never"))
    with pytest.raises(PolicyViolation):
        await run_turn("q", client=client(store, model), registry=default_registry(), ctx=ctx)


# ----------------------------------------------------------------- evidence binding (ADR-014)


def returned_texts(messages):
    return [
        str(p.content)
        for m in messages
        if isinstance(m, pa.ModelRequest)
        for p in m.parts
        if isinstance(p, pa.ToolReturnPart)
    ]


def system_text(messages):
    return "\n".join(
        p.content
        for m in messages
        if isinstance(m, pa.ModelRequest)
        for p in m.parts
        if isinstance(p, pa.SystemPromptPart)
    )


async def test_tool_results_carry_source_labels_and_kernel_results_do_not(
    ctx, store, kernel, tmp_path
):
    (tmp_path / "notes.txt").write_text("TYK2 is a JAK family kinase.")
    model, seen = scripted(
        tool_call("python_execute", {"code": "x"}),
        tool_call("read_file", {"path": "notes.txt"}),
        text("done"),
    )
    await run_turn("q", client=client(store, model), registry=default_registry(), ctx=ctx)
    run_result, file_result = returned_texts(seen[2])
    assert "[source" not in run_result
    assert file_result.startswith("<untrusted-tool-output>\n[source s1]\n")
    assert "bq.value" in system_text(seen[0]) and "[@s3" in system_text(seen[0])


async def test_binding_off_is_the_v01_behaviour(ctx, store, tmp_path):
    (tmp_path / "notes.txt").write_text("TYK2 is a JAK family kinase.")
    model, seen = scripted(tool_call("read_file", {"path": "notes.txt"}), text("{{nope}}"))
    turn = await run_turn(
        "q",
        client=client(store, model),
        registry=default_registry(),
        ctx=ctx,
        binding=False,
        binding_retries=0,
    )
    assert "[source" not in returned_texts(seen[1])[0]
    assert "bq.value" not in system_text(seen[0])
    assert turn.answer.text() == "{{nope}}" and len(seen) == 2


async def test_unresolved_bindings_are_sent_back(ctx, store, kernel):
    kernel.on("bq.value", Script(outputs=[value_output("n_de", 412, unit="genes")]))
    model, seen = scripted(
        text("1. {{n_genes}} genes changed."),
        tool_call("python_execute", {"code": "bq.value('n_de', 412, unit='genes')"}),
        text("1. {{n_de}} genes changed."),
    )
    turn = await run_turn(
        "q", client=client(store, model), registry=default_registry(), ctx=ctx, binding_retries=2
    )
    assert turn.answer.text() == "1. {{n_de}} genes changed." and turn.binding_retries == 1
    feedback = [e for e in store.read("S") if e.type == "user_message"][1]
    assert feedback.actor is Actor.SYSTEM and "n_genes" in feedback.parsed().message.text()
    last = seen[1][-1]
    assert isinstance(last, pa.ModelRequest)
    prompt = last.parts[-1]
    assert isinstance(prompt, pa.UserPromptPart) and "n_genes" in str(prompt.content)


async def test_binding_retries_are_capped(ctx, store):
    model, seen = scripted(text("{{nope}}"))  # never fixes it
    turn = await run_turn(
        "q", client=client(store, model), registry=default_registry(), ctx=ctx, binding_retries=2
    )
    assert turn.answer.text() == "{{nope}}" and turn.binding_retries == 2 and len(seen) == 3


async def test_unresolved_binding_does_not_trigger_a_retry_by_default(ctx, store):
    model, seen = scripted(text("{{nope}}"))
    turn = await run_turn("q", client=client(store, model), registry=default_registry(), ctx=ctx)
    assert "{{nope}}" in turn.answer.text()
    assert turn.binding_retries == 0 and len(seen) == 1


async def test_checkpoint_before_initial_setup_finishes_preparation_once(ctx, store):
    from bioquorum.core import events as ev
    from bioquorum.core.messages import Message

    original = store.append(
        ev.UserMessage(message=Message.user("q")), session_id=ctx.session_id, actor=Actor.USER
    )
    started = []

    async def prepare(asked):
        started.append(asked.id)

    model, _ = scripted(text("Done"))
    await run_turn(
        "q",
        client=client(store, model),
        registry=default_registry(),
        ctx=ctx,
        resume_events=[original],
        before_first_model=prepare,
    )
    assert started == [original.id]
    assert len(store.read(ctx.session_id, types=["user_message"])) == 1


async def test_resume_allows_kernel_and_read_tools_while_skipping_side_effects(
    ctx, store, kernel, tmp_path
):
    from bioquorum.core import events as ev
    from bioquorum.core.messages import Message

    (tmp_path / "data.txt").write_text("gene=TP53\n")
    ctx.classification = Classification.PUBLIC
    original = store.append(
        ev.UserMessage(message=Message.user("reload and finish")),
        session_id=ctx.session_id,
        actor=Actor.USER,
    )
    prior_py = store.append(
        ev.ToolCallRequested(
            call_id="c-py",
            tool="python_execute",
            args={"code": "x = 1"},
            permission=PermissionClass.EXECUTE,
        ),
        session_id=ctx.session_id,
        actor=Actor.AGENT,
    )
    prior_read = store.append(
        ev.ToolCallRequested(
            call_id="c-read",
            tool="read_file",
            args={"path": "data.txt"},
            permission=PermissionClass.READ_FS,
        ),
        session_id=ctx.session_id,
        actor=Actor.AGENT,
    )
    prior_dl = store.append(
        ev.ToolCallRequested(
            call_id="c-dl",
            tool="download_file",
            args={"url": "https://example.org/file.csv"},
            permission=PermissionClass.NETWORK,
        ),
        session_id=ctx.session_id,
        actor=Actor.AGENT,
    )
    model, _ = scripted(
        pa.ModelResponse(
            parts=[
                pa.ToolCallPart("python_execute", {"code": "x = 1"}, tool_call_id="r1"),
                pa.ToolCallPart("read_file", {"path": "data.txt"}, tool_call_id="r2"),
                pa.ToolCallPart(
                    "download_file", {"url": "https://example.org/file.csv"}, tool_call_id="r3"
                ),
            ]
        ),
        text("Done."),
    )
    results = []
    await run_turn(
        "reload and finish",
        client=client(store, model),
        registry=default_registry(),
        ctx=ctx,
        resume_events=[original, prior_py, prior_read, prior_dl],
        hooks=Hooks(on_tool_result=lambda c, r: results.append((c.name, r.is_error, r.text()))),
    )
    assert kernel.executed == ["x = 1"]
    assert results[0][0] == "python_execute" and not results[0][1]
    assert results[1][0] == "read_file" and not results[1][1] and "TP53" in results[1][2]
    assert results[2][0] == "download_file" and results[2][1] and "already entered" in results[2][2]


async def test_compaction_prefers_turn_boundary_without_duplicating_active_question(
    ctx, store, kernel
):
    from bioquorum.core import events as ev
    from bioquorum.core.messages import Message

    old = "earlier study " * 120
    store.append(ev.UserMessage(message=Message.user(old)), session_id="S", actor=Actor.USER)
    store.append(
        ev.AssistantMessage(message=Message.assistant("earlier findings " * 120)),
        session_id="S",
        actor=Actor.AGENT,
    )
    model, _ = scripted(
        tool_call("python_execute", {"code": "step1"}),
        text("summary of earlier study"),
        tool_call("python_execute", {"code": "step2"}),
        text("final answer"),
    )
    await run_turn(
        "active question",
        client=client(store, model),
        registry=default_registry(),
        ctx=ctx,
        compact_at_bytes=2000,
    )
    events = store.read("S")
    compacted = [e.parsed() for e in events if e.type == "context_compacted"]
    assert len(compacted) == 1
    active_user_seq = next(
        e.seq
        for e in events
        if e.type == "user_message" and e.parsed().message.text() == "active question"
    )
    assert compacted[0].to_seq == active_user_seq - 1
    projected = conversation(events, compacted=True)
    assert [m.text() for m in projected].count("active question") == 1


async def test_cancellation_interrupts_kernel_and_closes_all_pending_tool_calls(ctx, store, kernel):
    import asyncio

    kernel.queue(Script(hang=True))
    model, _ = scripted(
        pa.ModelResponse(
            parts=[
                pa.ToolCallPart("python_execute", {"code": "hang()"}, tool_call_id="c1"),
                pa.ToolCallPart("list_files", {"path": "."}, tool_call_id="c2"),
            ]
        )
    )
    task = asyncio.create_task(
        run_turn("q", client=client(store, model), registry=default_registry(), ctx=ctx)
    )
    while not kernel.executed:
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert kernel._interrupted.is_set()
    failed = [e.payload["call_id"] for e in store.read("S") if e.type == "tool_call_failed"]
    assert len(failed) == 2
    assert any(e.type == "run_cancelled" for e in store.read("S"))
