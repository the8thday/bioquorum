import json

import pytest

from bioquorum.application import programs, research, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.review.evidence import Evidence
from bioquorum.tools import ToolRegistry, invoke


@pytest.mark.anyio
async def test_new_program_session_is_atomic_and_optional_context_is_background(tmp_path):
    ws = workspace.init_project(tmp_path)
    controls = ResearchControls(ws)
    line = await programs.create(controls, "Validation")
    previous = await programs.new_session(controls, line["id"], "Initial intake")
    controls.store.append(
        ev.ClaimExtracted(
            claim_id="local-label",
            source_event_id="prior-answer",
            text="The recorded label is amber-violet.",
            kind=ClaimKind.PLAN,
        ),
        session_id=previous,
        actor=Actor.SYSTEM,
    )
    sid = await programs.new_session(controls, line["id"], "Follow-up")
    outside = controls.new_session("Elsewhere")
    other = await programs.create(controls, "Unrelated")
    foreign = await programs.new_session(controls, other["id"], "Other session")
    controls.store.append(
        ev.ClaimExtracted(
            claim_id="foreign-label",
            source_event_id="foreign-answer",
            text="Foreign copper label.",
            kind=ClaimKind.PLAN,
        ),
        session_id=foreign,
        actor=Actor.SYSTEM,
    )
    name = "read_program_context"
    for enabled, session, allowed in [
        (False, sid, False),
        (True, outside, False),
        (True, sid, True),
    ]:
        ws.config["agent"]["program_context"] = enabled
        registry, policy = ToolRegistry(), PermissionPolicy()
        research._project_context(ws, controls.store, session, registry, policy, ws.agent_options())
        assert (name in registry) == allowed
        if allowed:
            ctx = workspace.tool_context(ws, controls.store, session, policy)
            result = await invoke(registry.get(name), {"section": "claims"}, ctx)
            assert not result.is_error
            assert "amber-violet" in result.text() and "copper" not in result.text()
            record = json.loads(result.text())["items"][0]
            assert record["session_id"] == previous and record["status"] == "unverified"
            assert name not in [
                s.tool for s in Evidence.from_events(controls.store.history(sid)).sources
            ]
    sessions_before = controls.sessions()
    await programs.close(controls, line["id"])
    with pytest.raises(ValueError):
        await programs.new_session(controls, line["id"], "Should roll back")
    assert controls.sessions() == sessions_before
