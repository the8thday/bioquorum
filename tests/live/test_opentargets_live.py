"""Nightly: the real Open Targets MCP server still matches our recorded cassette."""

from pathlib import Path

import pytest

from bioquorum.mcp import McpConnection, ServerConfig
from bioquorum.mcp.replay import Cassette

pytestmark = [pytest.mark.live, pytest.mark.anyio]
OT = Path(__file__).parents[1] / "fixtures" / "mcp" / "opentargets.json"


async def test_live_tools_still_match_cassette():
    cfg = ServerConfig(
        name="opentargets", url="https://mcp.platform.opentargets.org/mcp", timeout_s=90
    )
    recorded = {t["name"]: t["input_schema"] for t in Cassette.load(OT).tools}
    async with McpConnection(cfg) as conn:
        live = {t.name: t.input_schema for t in await conn.list_tools()}
        r = await conn.call_tool("search_entities", {"query_strings": ["IL23R"]})
    assert set(recorded) <= set(live), "tools disappeared upstream: re-record the cassette"
    for name, schema in recorded.items():
        assert set(schema.get("required", [])) == set(live[name].get("required", [])), name
    assert "ENSG00000162594" in r.text()
