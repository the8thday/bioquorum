import pytest
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.application.workspace import init_project
from bioquorum.core.enums import Classification
from bioquorum.models import ModelClient, Router
from bioquorum.storage.events import EventStore


@pytest.mark.anyio
async def test_role_scores_recorded_without_changing_config(tmp_path):
    from bioquorum.application.model_eval import evaluate, results

    ws = init_project(tmp_path, classification=Classification.PUBLIC)
    ws.config = {
        "providers": {
            "openai": {},
            "local": {"kind": "openai", "allowed_classifications": ["internal"]},
        },
        "roles": {"worker": "openai:test"},
        "models": {"local:restricted": {}},
    }
    store = EventStore(ws.database())

    def reply(messages, info):
        return pa.ModelResponse(parts=[pa.TextPart(content="B")])

    client = ModelClient(Router(ws.models_config()), store, factory=lambda _: FunctionModel(reply))
    await evaluate(ws, store, client)
    report = results(ws, store)
    assert set(report["scores"]) == {"openai:test"}
    assert set(report["scores"]["openai:test"]) == {"worker", "planner", "summarizer", "reviewer"}
    assert ws.config["roles"] == {"worker": "openai:test"}


@pytest.mark.anyio
async def test_draft_uses_planner_route_and_remains_unapproved(tmp_path):
    from bioquorum.application.controls import ResearchControls

    ws = init_project(tmp_path, classification=Classification.PUBLIC)
    ws.config = {
        "providers": {"openai": {}},
        "roles": {"worker": "openai:worker", "planner": "openai:planner"},
    }

    def reply(messages, info):
        return pa.ModelResponse(parts=[pa.TextPart(content='{"steps":["Check the source"]}')])

    controls = ResearchControls(
        ws,
        make_client=lambda ws, store: ModelClient(
            Router(ws.models_config()), store, factory=lambda _: FunctionModel(reply)
        ),
    )
    sid = controls.new_session("Plan")
    result = await controls.draft_plan(sid, "Assess the sample")
    assert result["model"] == "openai:planner"
    assert result["steps"] == ["Check the source"]
    assert not controls.store.read(sid, types=["turn_started", "plan_approved"])


@pytest.mark.parametrize("parent_option", [False, True])
def test_models_eval_cli_honors_selected_project(tmp_path, monkeypatch, parent_option):
    import json

    from typer.testing import CliRunner

    from bioquorum.application import research
    from bioquorum.application.usage import report
    from bioquorum.application.workspace import load, start_session
    from bioquorum.cli.main import app

    init_project(tmp_path, classification=Classification.PUBLIC)
    (tmp_path / "bioquorum.toml").write_text('[providers.openai]\n[roles]\nworker="openai:test"\n')

    def make_client(ws, store):
        def reply(*_):
            return pa.ModelResponse(parts=[pa.TextPart(content="B")])

        return ModelClient(
            Router(ws.models_config()), store, factory=lambda _: FunctionModel(reply)
        )

    monkeypatch.setattr(research, "model_client", make_client)
    opts = ["--project", str(tmp_path)]
    args = ["models", *opts, "eval"] if parent_option else ["models", "eval", *opts]
    run = CliRunner().invoke(app, args)
    assert run.exit_code == 0, run.output
    assert json.loads(run.output)["scores"]["openai:test"]["worker"]["correct"] == 1
    ws = load(tmp_path)
    store = EventStore(ws.database())
    sid = start_session(ws, store, "A separate session")
    usage = report(ws, store, sid)
    assert usage["project"]["total"]["calls"] == 8
    assert usage["session"]["total"]["calls"] == 0
