import httpx2
import pytest

from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.server.app import create_app


@pytest.mark.anyio
async def test_unconfigured_project_can_open_independent_offline_example(tmp_path):
    c = ResearchControls(init_project(tmp_path, name="Empty workspace"))
    origin = "http://127.0.0.1:8111"
    app = create_app(c, origin[7:])
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=origin, headers={"Origin": origin}
        ) as http,
    ):
        project = (await http.get("/api/sessions")).json()
        assert project["onboarding"]["worker"] == "unconfigured"
        demo = (await http.get("/example/api/sessions")).json()
        assert demo["example"]["question"]
        assert (
            await http.post("/example/api/sessions", json={"title": "Offline only"})
        ).status_code == 201
        assert (await http.get("/api/sessions")).json()["sessions"] == []
