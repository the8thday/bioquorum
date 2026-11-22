import httpx2
import pytest

from bioquorum.application import project_library, research, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core.enums import ModelRole
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.server.app import create_app
from bioquorum.storage.rebuild import rebuild
from bioquorum.tools import ToolRegistry


@pytest.mark.anyio
async def test_project_settings_and_trash_are_scoped_to_one_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(project_library, "library_root", lambda: tmp_path / "library")
    monkeypatch.setenv("BQ_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret-never-return")
    ws = workspace.init_project(tmp_path / "root")
    host = "127.0.0.1:8100"
    app = create_app(ResearchControls(ws), host)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=f"http://{host}",
            headers={"Origin": f"http://{host}"},
        ) as http,
    ):
        body = {
            "name": "Target Validation",
            "classification": "internal",
            "instructions": "Use donor-level evidence.",
        }
        first, second = [(await http.post("/api/projects", json=body)).json() for _ in range(2)]
        url = first["href"]
        config = url + "api/settings"
        state = (await http.get(url + "api/sessions")).json()["onboarding"]
        assert state["worker"] == "unconfigured"
        assert state["credential"] == {"name": "OPENAI_API_KEY", "state": "set"}
        before = (await http.get(config)).json()
        assert before["project"]["instructions"] == body["instructions"]
        edit = {
            "revision": before["revision"],
            "patch": {
                "project": {"instructions": "Summarize by donor.", "display_name": "Primary"},
                "roles": {"worker": "openai:gpt-6.2-sol"},
            },
        }
        response = await http.post(config, json=edit)
        assert response.is_success, response.text
        assert (await http.post(config, json=edit)).status_code == 409
        state = (await http.get(url + "api/sessions")).json()["onboarding"]
        assert state["worker"] == "ready" and state["model"] == "openai:gpt-6.2-sol"
        monkeypatch.delenv("OPENAI_API_KEY")
        assert (await http.get(url + "api/sessions")).json()["onboarding"]["credential"][
            "state"
        ] == "missing"
        assert "fixture-secret-never-return" not in str((before, response.text, state))
        await http.post(url + "api/sessions", json={"title": "Keep me"})
        key = url.split("/")[2]
        delete = f"/api/projects/{key}/trash"
        assert (await http.post(delete, json={})).is_success
        assert (await http.get(url + "api/sessions")).status_code == 404
        assert second["href"] in (await http.get("/api/projects")).text
        trashed = (await http.get("/api/projects/trash")).json()
        assert [(p["name"], p["sessions"]) for p in trashed] == [("Primary", 1)]
        assert (await http.post(f"/api/projects/{key}/restore", json={})).is_success
        assert "Keep me" in (await http.get(url + "api/sessions")).text
        assert (await http.get(second["href"] + "api/settings")).json()["project"]["name"] == body[
            "name"
        ]
    path = project_library.project_path(key)
    loaded = workspace.load(path)
    rebuild(loaded.database())
    assert workspace.load(path).name == "Primary"
    assert str(loaded.models_config().roles[ModelRole.WORKER][0]) == "openai:gpt-6.2-sol"
    controls = ResearchControls(loaded)
    for _ in range(2):
        context = research._project_context(
            loaded,
            controls.store,
            controls.new_session("New session"),
            ToolRegistry(),
            PermissionPolicy(),
            loaded.agent_options(),
        )
        assert "Summarize by donor." in "\n".join(context)


@pytest.mark.anyio
async def test_instructions_reach_worker_and_planner_and_can_be_cleared(tmp_path):
    from pydantic_ai import messages as pa
    from pydantic_ai.models.function import FunctionModel

    from bioquorum.application import settings
    from bioquorum.core.enums import Classification
    from bioquorum.models import ModelClient, Router

    ws = workspace.init_project(tmp_path, classification=Classification.PUBLIC)
    ws.config = {
        "providers": {"openai": {}},
        "roles": {"worker": "openai:test"},
        "profile": {"name": "minimal"},
    }
    prompts = []

    def respond(messages, info):
        system = "\n".join(
            part.content
            for m in messages
            for part in m.parts
            if isinstance(part, pa.SystemPromptPart)
        )
        prompts.append(system)
        answer = '{"steps":["Read the file"]}' if "Propose a concise" in system else "Done."
        return pa.ModelResponse(parts=[pa.TextPart(content=answer)])

    c = ResearchControls(
        ws,
        make_client=lambda current, store: ModelClient(
            Router(current.models_config()), store, factory=lambda _: FunctionModel(respond)
        ),
    )
    revision = None
    for text in ("Write in Chinese.", ""):
        saved = settings.save(
            c, settings.SettingsEdit(revision=revision, patch={"project": {"instructions": text}})
        )
        revision = saved["revision"]
        prompts.clear()
        sid = c.new_session("Independent")
        await c.draft_plan(sid, "Read the file")
        await research.execute(
            ws,
            c.store,
            sid,
            "Read the file",
            client=c.make_client(ws, c.store),
            permissions=lambda servers: workspace.default_permissions(
                ws, allow=[], servers=servers
            ),
        )
        assert len(prompts) == 2
        assert all(("Write in Chinese." in p) == bool(text) for p in prompts)


def test_confidential_setup_respects_limits_and_preserves_existing_provider(tmp_path):
    from bioquorum.application import onboarding, settings
    from bioquorum.core.enums import Classification

    ws = workspace.init_project(tmp_path, classification=Classification.CONFIDENTIAL)
    ws.config["providers"] = {"ollama": {"base_url": "http://localhost:11500/v1"}}
    c = ResearchControls(ws)
    initial = settings.snapshot(c)
    assert "openai:gpt-6.2-sol" not in initial["models"]
    assert onboarding.overview(c)["credential"] is None

    def choose(spec):
        return settings.save(
            c, settings.SettingsEdit(revision=None, patch={"roles": {"worker": spec}})
        )

    with pytest.raises(ValueError):
        choose("openai:gpt-6.2-sol")
    choose("ollama:qwen3:8b")
    assert onboarding.overview(c)["credential"]["state"] == "not_required"
    assert ws.models_config().roles[ModelRole.WORKER][0].model == "qwen3:8b"
    assert ws.models_config().providers["ollama"].base_url == "http://localhost:11500/v1"


def test_locked_project_stays_in_library_and_invalid_text_is_rejected(tmp_path, monkeypatch):
    from bioquorum.core.enums import Classification

    monkeypatch.setattr(project_library, "library_root", lambda: tmp_path / "library")
    ws = workspace.init_project(tmp_path / "outside")
    with pytest.raises(ValueError, match="without credentials"):
        project_library.create("Unsafe", Classification.INTERNAL, "token=sample-value")
    entry = project_library.create("Keep", Classification.INTERNAL)
    key = entry["href"].split("/")[2]
    with workspace.project_lock(project_library.project_path(key)), pytest.raises(ValueError):
        project_library.move(key, restore=False, current=ws)
    assert project_library.project_path(key).is_dir()
    assert project_library.entries(ws, trashed=True) == []


def test_project_classification_is_event_backed_and_persists_across_rebuild(tmp_path):
    from bioquorum.application import extensions, settings
    from bioquorum.core.enums import Classification
    from bioquorum.storage.db import library_summary

    ws = workspace.init_project(tmp_path)
    controls = ResearchControls(ws)
    sid = controls.new_session("Keep this session")
    (tmp_path / "bioquorum.toml").write_text('[project]\nclassification="public"\n')
    assert workspace.load(tmp_path).classification == Classification.INTERNAL
    revision = None
    for level in (Classification.PUBLIC, Classification.CONFIDENTIAL):
        edit = settings.SettingsEdit(
            revision=revision, patch={"project": {"classification": level.value}}
        )
        saved = settings.save(controls, edit)
        assert ws.classification == level
        assert saved["project"]["classification"] == level.value
        assert extensions.snapshot(controls)["public_search"]["enabled"] == (
            level == Classification.PUBLIC
        )
        assert extensions.snapshot(controls)["public_search"]["available"] == (
            level == Classification.PUBLIC
        )
        assert workspace.load(tmp_path).classification == level
        assert controls.sessions()[0]["id"] == sid
        rebuild(ws.database())
        summary = library_summary(ws.state_dir / "bioquorum.db", ws.project_id)
        assert summary is not None and summary["classification"] == level
        revision = saved["revision"]
    with pytest.raises(ValueError):
        settings.save(
            controls,
            settings.SettingsEdit(
                revision=revision, patch={"project": {"classification": "unknown"}}
            ),
        )
    assert workspace.load(tmp_path).classification == Classification.CONFIDENTIAL
