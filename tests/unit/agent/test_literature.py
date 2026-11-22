"""Exercise scoped literature retrieval without external services."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.agent.budget import Budget, BudgetMeter
from bioquorum.agent.literature import literature_tool
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, PermissionClass, PermissionScope
from bioquorum.core.messages import Message
from bioquorum.models import ModelClient, ModelsConfig, Router
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.review.binding import find_bindings
from bioquorum.review.evidence import Evidence, source_aliases
from bioquorum.tools import ToolContext, ToolRegistry
from bioquorum.tools.invoke import invoke
from bioquorum.tools.spec import ToolResult, ToolSpec

pytestmark = pytest.mark.anyio
ABSTRACT = "The calibration workflow compared two reference samples throughout the study."
CONFIG = ModelsConfig.from_dict(
    {"providers": {"deepseek": {}}, "roles": {"worker": "deepseek:test"}}
)


class Search(BaseModel):
    query: str


def setup(store, *, allow: bool, answer: str = ABSTRACT):
    seen = []

    async def search(args: Search, ctx: ToolContext) -> ToolResult:
        seen.append(args.query)
        return ToolResult.text("PMID: 12345678\nWorkflow article\n" + ABSTRACT)

    registry = ToolRegistry(
        [
            ToolSpec(
                "lit__search",
                "Search public literature",
                search,
                PermissionClass.EXTERNAL_MCP,
                args_model=Search,
                target=lambda *_: "mcp:lit",
                classification_floor=Classification.PUBLIC,
            )
        ]
    )
    prompts = []

    def scripted(history, info):
        prompts.extend(history)
        assert "PRIVATE PARENT HISTORY" not in str(history)
        assert "private_note" not in str(info.function_tools)
        parts = [p for m in history for p in m.parts]
        if any(isinstance(p, pa.ToolReturnPart) for p in parts):
            content = json.dumps(
                {
                    "summary": "Child summary is not evidence.",
                    "quotes": [{"source": "s1", "quote": answer}],
                }
            )
            return pa.ModelResponse(parts=[pa.TextPart(content=content)])
        return pa.ModelResponse(
            parts=[pa.ToolCallPart("lit__search", {"query": "calibration"}, tool_call_id="remote")]
        )

    policy = PermissionPolicy()
    if allow:
        policy.grant(
            PermissionClass.EXTERNAL_MCP, "mcp:lit", PermissionScope.SESSION, session_id="parent"
        )
    policy.grant(
        PermissionClass.EXECUTE, "literature-subtask", PermissionScope.SESSION, session_id="parent"
    )
    ctx = ToolContext(
        store=store, session_id="parent", permissions=policy, classification=Classification.PUBLIC
    )
    client = ModelClient(Router(CONFIG), store, factory=lambda _: FunctionModel(scripted))
    meter = BudgetMeter(Budget())
    tool = literature_tool(registry, client, meter)
    return tool, ctx, meter, prompts, seen


async def test_only_verbatim_excerpt_is_exposed_and_budget_is_shared(store):
    tool, ctx, meter, prompts, seen = setup(store, allow=True)
    store.append(
        ev.UserMessage(message=Message.user("PRIVATE PARENT HISTORY")),
        session_id="parent",
        actor=Actor.USER,
    )
    result = await invoke(tool, {"question": "Find an article about sample calibration."}, ctx)
    assert not result.is_error
    assert meter.tokens > 0 and seen == ["calibration"] and prompts
    history = store.read("parent")
    finished = next(p for e in history if isinstance(p := e.parsed(), ev.LiteratureFinished))
    assert finished.outcome == "completed" and len(finished.sources) == 1
    assert set(source_aliases(history)) == {finished.sources[0].call_id}
    assert store.read(finished.task_id)
    evidence = Evidence.from_events(history)
    assert find_bindings(f'[@s1 "{ABSTRACT}"]', evidence)[0].ok
    assert not find_bindings('[@s1 "Child summary is not evidence."]', evidence)[0].ok


@pytest.mark.parametrize(
    ("allow", "quote"), [(False, ABSTRACT), (True, "An invented paper said nothing.")]
)
async def test_missing_permission_or_invented_quote_produces_no_evidence(store, allow, quote):
    tool, ctx, _, _, seen = setup(store, allow=allow, answer=quote)
    result = await invoke(tool, {"question": "Find a study."}, ctx)
    assert result.is_error
    assert bool(seen) is allow
    assert not Evidence.from_events(store.read("parent")).sources


async def test_shared_budget_can_stop_the_child_before_any_retrieval(store):
    tool, ctx, meter, _, seen = setup(store, allow=True)
    meter.limits = Budget(tokens=1)
    result = await invoke(tool, {"question": "Find a paper."}, ctx)
    assert result.is_error and not seen
    assert meter.stop_reason() == "token budget"
    assert not Evidence.from_events(store.read("parent")).sources


@pytest.mark.parametrize("lost_parent_result", [False, True])
async def test_parent_conversation_carries_only_validated_source_text(store, lost_parent_result):
    from bioquorum.agent.context import conversation
    from bioquorum.core.enums import MessageRole, Trust
    from bioquorum.core.messages import ToolCallPart

    tool, ctx, _, _, _ = setup(store, allow=True)
    main_call = ToolCallPart(
        name=tool.name, args={"question": "Find a paper."}, call_id="parent-call"
    )
    store.append(
        ev.AssistantMessage(message=Message(role=MessageRole.ASSISTANT, parts=[main_call])),
        session_id="parent",
        actor=Actor.AGENT,
    )
    await invoke(tool, main_call.args, ctx, call_id=main_call.call_id)
    history = store.read("parent")
    if lost_parent_result:
        history = [e for e in history if e.type != "tool_call_finished"]
        history.append(
            store.append(
                ev.ToolCallFailed(
                    call_id=main_call.call_id, tool=tool.name, error="Previous process exited"
                ),
                session_id="parent",
                actor=Actor.SYSTEM,
            )
        )
    messages = conversation(history, source_labels=True)
    if lost_parent_result:
        assert messages[-1].model_dump()["parts"][0]["trust"] == Trust.UNTRUSTED
    assert len(messages) == 2 and messages[-1].role is MessageRole.TOOL
    text = messages[-1].model_dump_json()
    assert "[source s1]" in text and ABSTRACT in text
    assert "Workflow article" not in text


@pytest.mark.parametrize("safe_read", [False, True])
async def test_two_general_tasks_keep_separate_sources_and_share_meter(store, safe_read):
    import asyncio

    from bioquorum.agent.subtasks import batch_tool

    _, ctx, meter, _, _ = setup(store, allow=True)

    active = peak = 0

    async def search(args: Search, child: ToolContext) -> ToolResult:
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.03)
        active -= 1
        return ToolResult.text(ABSTRACT)

    def script(history, info):
        if any(isinstance(p, pa.ToolReturnPart) for m in history for p in m.parts):
            return pa.ModelResponse(
                parts=[
                    pa.TextPart(
                        content=json.dumps(
                            {"summary": "recorded", "quotes": [{"source": "s1", "quote": ABSTRACT}]}
                        )
                    )
                ]
            )
        return pa.ModelResponse(parts=[pa.ToolCallPart("lookup", {"query": "calibration"})])

    registry = ToolRegistry(
        [
            ToolSpec(
                "lookup",
                "Find record",
                search,
                PermissionClass.CONNECTOR if safe_read else PermissionClass.EXTERNAL_MCP,
                parallel_read=safe_read,
                args_model=Search,
                target=lambda *_: "mcp:lit",
                classification_floor=Classification.PUBLIC,
            )
        ]
    )
    client = ModelClient(Router(CONFIG), store, factory=lambda _: FunctionModel(script))
    ctx.permissions.grant(
        PermissionClass.EXECUTE, "research-subtasks", PermissionScope.SESSION, session_id="parent"
    )
    ctx.permissions.grant(
        PermissionClass.CONNECTOR, "mcp:lit", PermissionScope.SESSION, session_id="parent"
    )
    tool = batch_tool(registry, client, meter, parallelism=2)
    result = await invoke(
        tool, {"tasks": [{"question": "Review dataset A"}, {"question": "Review dataset B"}]}, ctx
    )
    assert not result.is_error
    assert peak == (2 if safe_read else 1)
    rows = store.read("parent", types=["subtask_finished"])
    assert len({e.payload["task_id"] for e in rows}) == 2
    assert len(Evidence.from_events(store.read("parent")).sources) == 2
    assert all(e.payload["sources"][0]["text"] == ABSTRACT for e in rows)
    assert meter.tokens == sum(e.payload["input_tokens"] + e.payload["output_tokens"] for e in rows)


async def test_batch_schema_omits_specialist_field_when_switch_is_off(store):
    from bioquorum.agent.subtasks import batch_tool

    _, _, meter, _, _ = setup(store, allow=True)
    tool = batch_tool(ToolRegistry(), ModelClient(Router(CONFIG), store), meter, parallelism=2)
    assert "specialist" not in json.dumps(tool.input_schema)
