import json
from pathlib import Path

import pytest

from bioquorum.mcp import McpConnection, ServerConfig
from bioquorum.mcp.replay import Cassette, record

pytestmark = pytest.mark.anyio
OT = Path(__file__).parents[2] / "fixtures" / "mcp" / "opentargets.json"
SEARCH = {"query_strings": ["IL23R", "inflammatory bowel disease"]}


def ot_config() -> ServerConfig:
    return ServerConfig(name="opentargets", replay=str(OT))


async def test_recorded_opentargets_replays_offline():
    async with McpConnection(ot_config()) as conn:
        names = {t.name for t in await conn.list_tools()}
        assert {"search_entities", "query_open_targets_graphql"} <= names
        r = await conn.call_tool("search_entities", SEARCH)
    hits = json.loads(r.text())
    assert hits["IL23R"][0] == {"id": "ENSG00000162594", "type": "target"}


async def test_arg_order_does_not_matter_but_values_do():
    cassette = Cassette.load(OT)
    gql = next(c for c in cassette.calls if c["tool"] == "query_open_targets_graphql")
    reordered = dict(reversed(list(gql["args"].items())))
    async with McpConnection(ot_config()) as conn:
        assert not (await conn.call_tool(gql["tool"], reordered)).is_error
        miss = await conn.call_tool("search_entities", {"query_strings": ["NOT-RECORDED"]})
    assert miss.is_error and "no recorded response" in miss.text()


async def test_record_roundtrip(fake_ot, tmp_path):
    cfg = ServerConfig(name="ot", url="https://example.invalid/mcp")
    async with McpConnection(cfg, target=fake_ot) as conn:
        cassette = await record(conn, [("target_info", {"symbol": "IL23R"}), ("plot", {})])
    path = tmp_path / "c.json"
    cassette.save(path)
    async with McpConnection(ServerConfig(name="ot", replay=str(path))) as conn:
        assert (await conn.call_tool("target_info", {"symbol": "IL23R"})).items == [
            "IL23R: ENSG00000162594"
        ]
        img = await conn.call_tool("plot", {})
    assert img.images()[0].mime == "image/png"


def test_rerecording_replaces_same_call(tmp_path):
    from bioquorum.mcp import RemoteResult

    c = Cassette(server="s")
    c.add_call("t", {"a": 1, "b": 2}, RemoteResult(items=["old"]))
    c.add_call("t", {"b": 2, "a": 1}, RemoteResult(items=["new"]))
    assert len(c.calls) == 1 and c.calls[0]["result"]["items"] == [{"text": "new"}]


BIOMCP = Path(__file__).parents[2] / "fixtures" / "mcp" / "biomcp.json"


async def test_recorded_biomcp_replays_offline():
    cfg = ServerConfig(name="biomcp", replay=str(BIOMCP))
    async with McpConnection(cfg) as conn:
        names = {t.name for t in await conn.list_tools()}
        assert {"search", "get"} <= names
        r = await conn.call_tool("get", {"entity": "article", "id": "28776086"})
    assert "The mechanisms of action of metformin" in r.text()
    assert "10.1007/s00125-017-4342-z" in r.text()


def test_cassettes_do_not_leak_local_paths():
    for path in (OT, BIOMCP):
        assert "/home/" not in Cassette.load(path).source


async def test_record_stores_command_basename_only(fake_ot):
    cfg = ServerConfig(name="x", command="/home/someone/.local/bin/tool", args=("serve",))
    async with McpConnection(cfg, target=fake_ot) as conn:
        cassette = await record(conn, [])
    assert cassette.source == "tool serve"
