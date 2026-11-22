import pytest

from bioquorum.application import settings, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core.enums import Classification, ModelRole


def test_register_new_gateway_and_reuse_after_restart(tmp_path):
    ws = workspace.init_project(tmp_path)
    controls = ResearchControls(ws)
    connection = {
        "kind": "openai-compatible",
        "name": "lab",
        "model": "org/alpha-v2",
        "base_url": "https://models.example.org/v1",
        "api": "responses",
        "role": "worker",
    }
    saved = settings.save(
        controls, settings.SettingsEdit(revision=None, patch={"connection": connection})
    )
    cfg = workspace.load(tmp_path).models_config()
    assert str(cfg.roles[ModelRole.WORKER][0]) == "lab:org/alpha-v2"
    assert cfg.providers["lab"].api_key == "env:BQ_LAB_API_KEY"
    assert cfg.providers["lab"].api == "responses"
    assert saved["roles"]["worker"] == "lab:org/alpha-v2"
    for invalid in (
        {"base_url": "https://secret@models.example.org/v1"},
        {"base_url": "http://remote.example/v1"},
        {"name": "openai"},
        {"name": "chatgpt"},
        {"api_key": "env:OTHER_KEY"},
    ):
        with pytest.raises(ValueError):
            settings.save(
                controls,
                settings.SettingsEdit(
                    revision=saved["revision"], patch={"connection": {**connection, **invalid}}
                ),
            )


def test_plan_is_remote_and_distinct_from_openai_api(tmp_path):
    ws = workspace.init_project(tmp_path, classification=Classification.CONFIDENTIAL)
    c = ResearchControls(ws)
    edit = settings.SettingsEdit(
        revision=None,
        patch={"connection": {"kind": "chatgpt", "model": "account-model", "role": "worker"}},
    )
    with pytest.raises(ValueError):
        settings.save(c, edit)
    ws.classification = Classification.INTERNAL
    settings.save(c, edit)
    cfg = ws.models_config()
    assert str(cfg.roles[ModelRole.WORKER][0]) == "chatgpt:account-model"
    assert cfg.providers["chatgpt"].api_key == "none"
    assert not cfg.providers["chatgpt"].is_local


@pytest.mark.anyio
async def test_openai_catalog_uses_project_key_and_checks_classification(tmp_path, monkeypatch):
    import httpx2

    from bioquorum.server.app import create_app

    workspace.init_project(tmp_path)
    (tmp_path / "bioquorum.toml").write_text(
        '[providers.lab]\nkind="openai"\nbase_url="https://catalog.example/v1"\n'
        'api_key="env:BQ_CATALOG_KEY"\n'
    )
    ws = workspace.load(tmp_path)
    monkeypatch.delenv("BQ_CATALOG_KEY", raising=False)
    calls = []

    async def upstream(self, request):
        assert str(request.url) == "https://catalog.example/v1/models"
        assert request.headers["authorization"] == "Bearer catalog-fixture"
        calls.append(request.method)
        if len(calls) > 1:
            return httpx2.Response(401, json={"error": "catalog-fixture"})
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": name, "object": "model", "created": 1, "owned_by": "account"}
                    for name in ("text-embedding-fixture", "ft:custom", "new-research-model")
                ],
            },
        )

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", upstream)
    origin = "http://127.0.0.1:8181"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=create_app(ResearchControls(ws), origin[7:])),
        base_url=origin,
        headers={"Origin": origin},
    ) as http:
        path = "/api/settings/openai-models/lab"
        missing = await http.post(path, json={})
        assert missing.status_code == 400 and "BQ_CATALOG_KEY" in missing.text
        monkeypatch.setenv("BQ_CATALOG_KEY", "catalog-fixture")
        result = await http.post(path, json={})
        assert result.json() == ["ft:custom", "new-research-model", "text-embedding-fixture"]
        denied = await http.post(path, json={})
        assert denied.status_code == 400 and "catalog-fixture" not in denied.text
        ws.classification = Classification.CONFIDENTIAL
        assert (await http.post(path, json={})).status_code == 400
        assert calls == ["GET", "GET"]
