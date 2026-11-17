"""Nightly: the locally installed BioMCP still matches our recorded cassette."""

import shutil
from pathlib import Path

import pytest

from bioquorum.mcp import McpConnection, ServerConfig
from bioquorum.mcp.replay import Cassette

BIN = shutil.which("biomcp")
pytestmark = [
    pytest.mark.live,
    pytest.mark.anyio,
    pytest.mark.skipif(BIN is None, reason="biomcp not installed (uv tool install biomcp-cli)"),
]
CASSETTE = Path(__file__).parents[1] / "fixtures" / "mcp" / "biomcp.json"


async def test_live_tools_still_match_cassette():
    assert BIN is not None
    cfg = ServerConfig(name="biomcp", command=BIN, args=("serve",), timeout_s=120)
    recorded = {t["name"]: t["input_schema"] for t in Cassette.load(CASSETTE).tools}
    async with McpConnection(cfg) as conn:
        live = {t.name: t.input_schema for t in await conn.list_tools()}
        r = await conn.call_tool("get", {"entity": "article", "id": "28776086"})
    assert set(recorded) <= set(live), "tools disappeared upstream: re-record the cassette"
    for name, schema in recorded.items():
        assert set(schema.get("required", [])) == set(live[name].get("required", [])), name
    assert "metformin" in r.text().lower()
