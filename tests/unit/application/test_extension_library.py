import base64
import io
import zipfile

import httpx2
import pytest

from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project, load
from bioquorum.server.app import create_app

ORIGIN = "http://127.0.0.1:8181"
DESCRIPTOR = b"---\nname: read-table\ndescription: Read a data table\n---\nSee references/guide.md"


@pytest.mark.anyio
async def test_manage_extensions_from_empty_project(tmp_path):
    init_project(tmp_path)
    (tmp_path / "bioquorum.toml").write_text('[mcp.servers.original]\ncommand="original"\n')
    controls = ResearchControls(load(tmp_path))
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("read-table/SKILL.md", DESCRIPTOR)
        archive.writestr("read-table/references/guide.md", "Use the header.")
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=create_app(controls, ORIGIN[7:])),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
    ) as http:
        path = "/api/extensions"
        state = (await http.get(path)).json()

        async def edit(action, **fields):
            response = await http.post(
                path, json=dict(revision=state["revision"], action=action, **fields)
            )
            assert response.status_code == 200, response.text
            return response.json()

        state = await edit(
            "install_skill",
            filename="sample.skill",
            content=base64.b64encode(package.getvalue()).decode(),
        )
        ws = load(tmp_path)
        assert ws.agent_options().skills
        assert (
            ws.project_skills()["read-table"].load("references/guide.md").text == "Use the header."
        )
        stale = await http.post(
            path, json={"action": "disable_skill", "name": "read-table", "revision": None}
        )
        assert stale.status_code == 409
        state = await edit("disable_skill", name="read-table")
        assert not load(tmp_path).project_skills()
        state = await edit("enable_skill", name="read-table")
        state = await edit("add_connector", name="remote", url="https://example.org/mcp")
        assert {s.name for s in load(tmp_path).mcp_servers()} == {"original", "remote"}
        state = await edit("disable_connector", name="original")
        state = await edit("remove_connector", name="remote")
        assert not load(tmp_path).mcp_servers()
        state = await edit("add_connector", name="local", command="fixture-mcp", args=["--stdio"])
        local = load(tmp_path).mcp_servers()[0]
        assert local.isolated and local.args == ("--stdio",)
        state = await edit("remove_skill", name="read-table")
        assert not state["skills"] and not load(tmp_path).project_skills()
        for fields in [
            {"action": "add_connector", "name": "unsafe", "url": "https://host/mcp?token=private"},
            {"action": "install_skill", "filename": "SKILL.md", "content": "invalid!"},
        ]:
            assert (
                await http.post(path, json={**fields, "revision": state["revision"]})
            ).status_code == 400


def test_package_only_imports_safe_text():
    from bioquorum.skills.package import unpack

    def archive(member):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as package:
            package.writestr("read-table/SKILL.md", DESCRIPTOR)
            package.writestr(member, "extra")
        return buffer.getvalue()

    _, files = unpack("table.zip", archive("read-table/scripts/run.py"))
    assert list(files) == ["SKILL.md"]
    with pytest.raises(ValueError):
        unpack("table.zip", archive("read-table/references/../outside.md"))
