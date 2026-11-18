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
    turn = await run_turn("q", client=client(store, model), registry=default_registry(), ctx=ctx)
    assert turn.answer.text() == "1. {{n_de}} genes changed." and turn.binding_retries == 1
    feedback = [e for e in store.read("S") if e.type == "user_message"][1]
    assert feedback.actor is Actor.SYSTEM and "n_genes" in feedback.parsed().message.text()
    last = seen[1][-1]
    assert isinstance(last, pa.ModelRequest)
    prompt = last.parts[-1]
    assert isinstance(prompt, pa.UserPromptPart) and "n_genes" in str(prompt.content)


async def test_binding_retries_are_capped(ctx, store):
    model, seen = scripted(text("{{nope}}"))  # never fixes it
    turn = await run_turn("q", client=client(store, model), registry=default_registry(), ctx=ctx)
    assert turn.answer.text() == "{{nope}}" and turn.binding_retries == 2 and len(seen) == 3
