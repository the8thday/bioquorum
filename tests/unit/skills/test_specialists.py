from bioquorum.core.enums import PermissionClass
from bioquorum.tools import ToolRegistry
from bioquorum.tools.spec import ToolSpec


def test_specialist_intersects_tools_and_permission_classes(tmp_path):
    from bioquorum.skills.specialists import load_specialists

    root = tmp_path / ".bioquorum" / "agents"
    root.mkdir(parents=True)
    (root / "reader.md").write_text(
        "---\nname: reader\ndescription: Read the local sources\ntools: [archive, remote, extra]\n"
        "permissions: [read_fs]\nmodel: 'openai:reader'\n---\nCheck the archive wording."
    )
    loaded = load_specialists(root)
    reader = loaded["reader"]
    assert reader.model == "openai:reader"
    assert reader.instructions == "Check the archive wording."
    from bioquorum.agent.subtasks import specialist_registry

    async def unused(*_):
        raise AssertionError("Registry selection must not execute tools")

    tools = ToolRegistry(
        ToolSpec(name, name, unused, perm, raw_input_schema={})
        for name, perm in [
            ("archive", PermissionClass.READ_FS),
            ("remote", PermissionClass.CONNECTOR),
        ]
    )
    assert [t.name for t in specialist_registry(tools, reader)] == ["archive"]
    (root / "external.md").symlink_to(root / "reader.md")
    assert set(load_specialists(root)) == {"reader"}
