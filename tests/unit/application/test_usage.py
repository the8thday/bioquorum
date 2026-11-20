import json

import httpx2
import pytest
from typer.testing import CliRunner

from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.cli.main import app
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, ModelRole
from bioquorum.server.app import create_app


@pytest.mark.anyio
async def test_project_session_and_turn_usage_match_cli(tmp_path):
    ws = init_project(tmp_path)
    controls = ResearchControls(ws)
    sid = controls.new_session("Original")

    def append(payload, session=sid):
        return controls.store.append(payload, session_id=session, actor=Actor.SYSTEM)

    def call(session, role, cost, *, known=True):
        append(
            ev.ModelCallFinished(
                call_id=str(controls.store.count()),
                provider="fixture",
                model="text",
                role=role,
                classification=Classification.INTERNAL,
                input_tokens=10,
                output_tokens=5,
                cache_read_tokens=3,
                cache_write_tokens=1,
                latency_ms=35,
                cost_usd=cost,
                cost_known=known,
            ),
            session,
        )

    call(sid, ModelRole.WORKER, 0.25)
    child = controls.fork(sid, "Fork")
    turn = append(ev.TurnRequested(question="Next question"))
    append(ev.TurnStarted(turn_id=turn.id))
    call(sid, ModelRole.WORKER, 0, known=False)
    append(ev.LiteratureStarted(task_id="sub", parent_call_id="tool", question="Find evidence"))
    call("sub", ModelRole.WORKER, 0)
    call(sid, ModelRole.REVIEWER, 0.1)
    append(ev.TurnFinished(turn_id=turn.id, outcome="completed"))
    call(child, ModelRole.SUMMARIZER, 0.05)
    transport = httpx2.ASGITransport(app=create_app(controls, "127.0.0.1:8181"))
    async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8181") as http:
        res = await http.get(f"/api/sessions/{sid}/usage")
    assert res.status_code == 200
    report = res.json()
    assert report["turn"]["total"]["calls"] == 3
    assert report["session"]["total"]["input_tokens"] == 40
    assert report["project"]["total"]["calls"] == 5
    assert report["project"]["total"]["latency_ms"] == 175
    assert report["turn"]["total"]["cache_read_tokens"] == 9
    assert report["turn"]["roles"]["worker"]["unknown_prices"] == 1
    assert report["turn"]["roles"]["subtask"]["zero_prices"] == 1
    assert report["turn"]["total"]["known_usd"] == pytest.approx(0.1)
    cli = CliRunner().invoke(app, ["usage", sid, "-C", str(tmp_path)])
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.stdout)["session"] == report["session"]
