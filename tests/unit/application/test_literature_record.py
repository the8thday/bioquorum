"""The parent answer can dereference a child source without exposing the child session."""

import pytest

from bioquorum.application import record, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, TextPart, ToolResultPart


@pytest.mark.parametrize(
    ("question", "message"),
    [("/literature", "1 to 1200"), ("/literature search with token=sample-value", "credentials")],
)
def test_invalid_explicit_question_is_rejected_before_recording(tmp_path, question, message):
    ws = workspace.init_project(tmp_path)
    ws.config = {"agent": {"literature_subtasks": True}}
    controls = ResearchControls(ws)
    sid = controls.new_session("Evidence record")
    before = controls.store.read(sid)
    with pytest.raises(ValueError, match=message) as error:
        controls.submit(sid, question)
    assert "sample-value" not in str(error.value)
    assert controls.store.read(sid) == before


def test_evidence_drawer_resolves_child_source_only_when_answer_is_bound(tmp_path):
    ws = workspace.init_project(tmp_path)
    controls = ResearchControls(ws)
    sid = controls.new_session("Evidence record")
    other = controls.new_session("Unrelated")
    task_id = new_id()
    quote = "The reported measure was observed in the designated reference samples."
    source = "Title and known provenance. " + quote
    origin = controls.store.append(
        ev.ToolCallFinished(
            call_id="actual-tool-call",
            tool="literature__get",
            result=ToolResultPart(call_id="actual-tool-call", content=[TextPart(text=source)]),
        ),
        session_id=task_id,
        actor=Actor.TOOL,
    )
    controls.store.append(
        ev.LiteratureFinished(
            task_id=task_id,
            parent_call_id="parent",
            outcome="completed",
            sources=[
                ev.LiteratureExcerpt(
                    call_id="actual-tool-call",
                    source_event_id=origin.id,
                    tool="literature__get",
                    text=quote,
                )
            ],
        ),
        session_id=sid,
        actor=Actor.SYSTEM,
    )
    saved = controls.store.append(
        ev.AssistantMessage(
            message=Message.assistant(f'The measure was documented [@s1 "{quote}"].')
        ),
        session_id=sid,
        actor=Actor.AGENT,
    )
    payload = record.binding(controls, sid, saved.id, "actual-tool-call", "quote")
    assert payload["source"] == source and payload["task_id"] == task_id
    with pytest.raises(LookupError):
        record.binding(controls, other, saved.id, "actual-tool-call", "quote")


@pytest.mark.anyio
@pytest.mark.parametrize("explicit", [False, True])
async def test_config_routes_through_child_and_parent_can_bind(tmp_path, explicit):
    import json

    from pydantic import BaseModel
    from pydantic_ai import messages as pa
    from pydantic_ai.models.function import FunctionModel

    from bioquorum.application import research
    from bioquorum.core.enums import Classification, PermissionClass, PermissionScope
    from bioquorum.models import ModelClient, ModelsConfig, Router
    from bioquorum.policy.permissions import PermissionPolicy
    from bioquorum.runtime import FakeKernel
    from bioquorum.tools import ToolRegistry
    from bioquorum.tools.spec import ToolResult, ToolSpec

    name = "literature__get"
    quote = "The run used a machined reference tile for the optical check."
    original = "Complete retrieved record\n" + quote + "\nAdditional editorial material."
    ws = workspace.init_project(tmp_path, classification=Classification.PUBLIC)
    ws.config = {"agent": {"literature_subtasks": True, "literature_tools": [name]}}
    controls = ResearchControls(ws)
    sid = controls.new_session("Parent")
    cfg = ModelsConfig.from_dict(
        {"providers": {"deepseek": {}}, "roles": {"worker": "deepseek:test"}}
    )

    class Retrieval(BaseModel):
        query: str

    async def retrieval(args, ctx):
        assert ctx.session_id != sid and ctx.kernels is None and ctx.cas is None
        return ToolResult.text(original)

    def registry():
        return ToolRegistry(
            [
                ToolSpec(
                    name,
                    "Read an approved publication record",
                    retrieval,
                    PermissionClass.EXTERNAL_MCP,
                    args_model=Retrieval,
                    target=lambda *_: "mcp:archive",
                )
            ]
        )

    def permissions(_):
        policy = PermissionPolicy()
        policy.grant(
            PermissionClass.EXTERNAL_MCP, "mcp:archive", PermissionScope.SESSION, session_id=sid
        )
        return policy

    def scripted(history, info):
        parts = [p for msg in history for p in msg.parts]
        system = next(p.content for p in parts if isinstance(p, pa.SystemPromptPart))
        returned = [p for p in parts if isinstance(p, pa.ToolReturnPart)]
        options = {tool.name for tool in info.function_tools}
        child = system.startswith("You are a literature retrieval helper.")
        if explicit and not child:
            assert options == {name}
            assert quote in str(parts) and "Additional editorial material." not in str(parts)
            part = pa.TextPart(content=f'The optical check had a reference [@s1 "{quote}"].')
        elif not returned:
            assert options == ({name} if child else {name, "delegate_literature"})
            tool = name if child else "delegate_literature"
            args = (
                {"query": "optical check"}
                if child
                else {"question": "Find the reference for the optical check"}
            )
            part = pa.ToolCallPart(tool, args, tool_call_id="requested")
        elif child:
            assert "Additional editorial material." in str(returned)
            part = pa.TextPart(
                content=json.dumps(
                    {
                        "summary": "The lookup found the reference for the optical check.",
                        "quotes": [{"source": "s1", "quote": quote}],
                    }
                )
            )
        else:
            assert quote in str(returned) and "Additional editorial material." not in str(returned)
            part = pa.TextPart(content=f'The optical check had a reference [@s1 "{quote}"].')
        return pa.ModelResponse(parts=[part])

    async def factory(_language):
        return FakeKernel()

    result = await research.execute(
        ws,
        controls.store,
        sid,
        "/literature What did the optical check use?"
        if explicit
        else "What did the optical check use?",
        client=ModelClient(Router(cfg), controls.store, factory=lambda _: FunctionModel(scripted)),
        permissions=permissions,
        kernel_factory=factory,
        registry_factory=registry,
    )
    report = next(
        p for e in controls.store.read(sid) if isinstance(p := e.parsed(), ev.LiteratureFinished)
    )
    assert report.outcome == "completed" and report.prefetched is explicit
    drawer = record.binding(
        controls, sid, result.answer_event_id, report.sources[0].call_id, "quote"
    )
    assert drawer["source"] == original
    assert len(controls.sessions()) == 1
