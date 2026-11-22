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


def test_package_rejects_oversized_decompressed_entry_with_spoofed_header(monkeypatch):
    from bioquorum.skills.loader import MAX_TEXT
    from bioquorum.skills.package import unpack

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("read-table/SKILL.md", DESCRIPTOR)
        package.writestr("read-table/references/big.md", "A" * (MAX_TEXT * 4))

    with pytest.raises(ValueError, match="Duplicate or oversized"):
        unpack("table.zip", buffer.getvalue())

    real_infolist = zipfile.ZipFile.infolist

    def spoofed_infolist(self):
        entries = real_infolist(self)
        for entry in entries:
            if entry.filename.endswith("big.md"):
                entry.file_size = 10
        return entries

    monkeypatch.setattr(zipfile.ZipFile, "infolist", spoofed_infolist)
    with pytest.raises(ValueError):
        unpack("table.zip", buffer.getvalue())


def test_install_skill_allows_symlinked_project_ancestor_but_rejects_symlinked_store(tmp_path):
    from bioquorum.application import extensions

    real_project = tmp_path / "real"
    init_project(real_project)
    alias = tmp_path / "alias"
    alias.symlink_to(real_project, target_is_directory=True)
    ws = load(alias)
    ws.root = alias  # keep unresolved ancestor path as a caller might pass
    controls = ResearchControls(ws)
    content = base64.b64encode(DESCRIPTOR).decode()

    extensions.save(
        controls,
        extensions.Edit(
            revision=None,
            action="install_skill",
            filename="SKILL.md",
            content=content,
        ),
    )
    entry = controls.ws.config["extensions"]["skills"]["read-table"]
    assert (
        real_project
        / ".bioquorum"
        / "installed-skills"
        / entry["version"]
        / "read-table"
        / "SKILL.md"
    ).is_file()

    bad_project = tmp_path / "bad"
    init_project(bad_project)
    outside = tmp_path / "outside"
    outside.mkdir()
    (bad_project / ".bioquorum" / "installed-skills").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="local directory"):
        extensions.save(
            ResearchControls(load(bad_project)),
            extensions.Edit(
                revision=None,
                action="install_skill",
                filename="SKILL.md",
                content=content,
            ),
        )


def test_included_skill_installs_and_reopens_in_an_existing_project(tmp_path):
    from bioquorum.application import extensions

    ws = init_project(tmp_path)
    controls = ResearchControls(ws)
    state = extensions.snapshot(controls)
    assert {"skill-creator", "literature-record-lookup", "pdb-record-lookup"} <= {
        item["name"] for item in state["included_skills"]
    }
    assert "skill-creator" not in ws.project_skills()
    state = extensions.save(
        controls,
        extensions.Edit(revision=None, action="install_included_skill", name="skill-creator"),
    )
    reopened = load(tmp_path)
    assert reopened.agent_options().skills
    assert reopened.project_skills()["skill-creator"].load().text
    assert "skill-creator" not in {s["name"] for s in state["included_skills"]}
    with pytest.raises(ValueError):
        extensions.save(
            controls,
            extensions.Edit(
                revision=state["revision"], action="install_included_skill", name="../private"
            ),
        )


def test_public_search_is_visible_and_scoped_to_public_projects(tmp_path, monkeypatch):
    from bioquorum.application import extensions, project_library
    from bioquorum.core.enums import Classification

    monkeypatch.setattr(project_library, "library_root", lambda: tmp_path / "library")
    monkeypatch.setenv("BQ_CONFIG", str(tmp_path / "user.toml"))
    for level in (Classification.PUBLIC, Classification.INTERNAL):
        created = project_library.create("Search", level)
        root = project_library.project_path(created["href"].split("/")[2])
        controls = ResearchControls(load(root))
        state = extensions.snapshot(controls)
        public = level == Classification.PUBLIC
        assert state["public_search"] == {"enabled": public, "available": public}
        if public:
            for enabled in (False, True):
                state = extensions.save(
                    controls,
                    extensions.Edit(
                        revision=state["revision"], action="set_public_search", enabled=enabled
                    ),
                )
                assert load(root).config["connectors"]["public_sources"] is enabled
        else:
            with pytest.raises(ValueError):
                extensions.save(
                    controls,
                    extensions.Edit(revision=None, action="set_public_search", enabled=True),
                )
