"""Check that the public workbench project can retrieve a gene and its literature."""

import shutil
from pathlib import Path

import pytest

from bioquorum.application import workspace
from bioquorum.core.enums import Classification
from bioquorum.core.messages import TextPart
from bioquorum.storage.events import EventStore
from bioquorum.tools import ToolRegistry, invoke
from bioquorum.tools.external import register_external

pytestmark = [pytest.mark.live, pytest.mark.anyio]
SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "workbench-v07" / "bioquorum.toml"


async def test_public_workbench_il11(tmp_path):
    workspace.init_project(tmp_path, classification=Classification.PUBLIC)
    shutil.copyfile(SAMPLE, tmp_path / "bioquorum.toml")
    ws = workspace.load(tmp_path)
    store = EventStore(ws.database())
    session = workspace.start_session(ws, store, "Public lookup")
    servers = {config.name: config for config in ws.mcp_servers()}
    permissions = workspace.default_permissions(ws, allow=[], servers=list(servers.values()))
    ctx = workspace.tool_context(ws, store, session, permissions)

    async def lookup(registry, tool, arguments):
        result = await invoke(registry.get(tool), arguments, ctx)
        assert not result.is_error
        return " ".join(part.text for part in result.content if isinstance(part, TextPart))

    registry = ToolRegistry()
    async with ws.mcp_connection(servers["opentargets"]) as connection:
        await register_external(registry, connection)
        gene = await lookup(registry, "opentargets__search_entities", {"query_strings": ["IL11"]})
        assert "ENSG00000095752" in gene

    async with ws.mcp_connection(servers["pubmed"]) as connection:
        await register_external(registry, connection)
        articles = await lookup(
            registry, "pubmed__search_articles", {"query": "31554736[PMID] AND interleukin-11"}
        )
        assert "31554736" in articles
        record = await lookup(registry, "pubmed__get_article_metadata", {"pmids": ["31554736"]})
        assert "31554736" in record and "interleukin-11" in record.lower()


async def test_registered_connector_discovers_tools(tmp_path):
    from bioquorum.application import extensions, settings
    from bioquorum.application.controls import ResearchControls

    ws = workspace.init_project(tmp_path, classification=Classification.PUBLIC)
    controls = ResearchControls(ws)
    extensions.save(
        controls,
        extensions.Edit(
            revision=None,
            action="add_connector",
            name="pubmed",
            url="https://pubmed.mcp.claude.com/mcp",
        ),
    )
    sid = controls.new_session("Test registered connection")
    await settings.reconnect(controls, sid, "pubmed")
    (entry,) = extensions.snapshot(controls)["connectors"]
    assert entry["status"] == "reachable" and entry["tools"] > 0
