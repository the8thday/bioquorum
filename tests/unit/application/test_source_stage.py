"""Research sources may be staged as scoped, hash-checked kernel inputs."""

import pytest

from bioquorum.application import workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, PermissionClass, PermissionScope
from bioquorum.core.messages import DataRefPart, TextPart, ToolResultPart
from bioquorum.runtime.fake import FakeKernel, Script
from bioquorum.tools.builtin.kernel import PYTHON_EXECUTE
from bioquorum.tools.invoke import invoke
from bioquorum.tools.source_stage import SOURCE_STAGE_TARGET, source_stage_tool


@pytest.mark.anyio
async def test_staged_source_is_an_input_and_cannot_be_changed_or_reused_cross_session(tmp_path):
    project = workspace.init_project(tmp_path, classification=Classification.PUBLIC)
    controls = ResearchControls(project)
    sid = controls.new_session("Research")
    other = controls.new_session("Unrelated")
    tool = source_stage_tool(lambda alias: '{"rows":[2,3]}' if alias == "s1" else None)
    code = "pass"
    expected_kernel = FakeKernel()
    expected_kernel.on(code, Script())

    async def kernel(_language):
        return expected_kernel

    def context(session):
        policy = workspace.default_permissions(project, allow=[], servers=[])
        policy.grant(
            PermissionClass.WRITE_FS,
            SOURCE_STAGE_TARGET,
            PermissionScope.SESSION,
            session_id=session,
        )
        return workspace.tool_context(
            project, controls.store, session, policy, kernel_factory=kernel
        )

    ctx = context(sid)
    try:
        result = await invoke(tool, {"alias": "s1"}, ctx)
        assert not result.is_error
        info = next(x for x in result.content if isinstance(x, DataRefPart))
        path = info.summary.split(",", 1)[0]
        assert (tmp_path / path).read_text() == '{"rows":[2,3]}'
        execution = {"code": code, "input_files": [path]}
        assert (await invoke(PYTHON_EXECUTE, execution, context(other))).is_error
        assert not (await invoke(PYTHON_EXECUTE, execution, ctx)).is_error
        run = next(
            p for e in controls.store.read(sid) if isinstance(p := e.parsed(), ev.RunStarted)
        )
        assert run.input_paths == [path] and run.inputs == [info.ref]
        (tmp_path / path).write_text("altered")
        assert (await invoke(PYTHON_EXECUTE, execution, ctx)).is_error
    finally:
        assert ctx.kernels is not None
        await ctx.kernels.shutdown_all()


def test_stage_tool_result_has_no_evidence_alias():
    from bioquorum.review.evidence import Evidence

    event = ev.Event.build(
        ev.ToolCallFinished(
            call_id="stage-call",
            tool="stage_tool_source",
            result=ToolResultPart(
                call_id="stage-call", content=[TextPart(text="staging metadata")]
            ),
        ),
        session_id="session",
        actor=Actor.TOOL,
        seq=1,
    )
    assert not Evidence.from_events([event]).sources
