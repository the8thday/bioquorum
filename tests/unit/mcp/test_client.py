import pytest

from bioquorum.core.enums import Classification
from bioquorum.mcp import McpConnection, RemoteImage, ServerConfig

from .conftest import PNG

pytestmark = pytest.mark.anyio
CFG = ServerConfig(name="ot", url="https://example.invalid/mcp")


async def test_list_and_call(fake_ot):
    async with McpConnection(CFG, target=fake_ot) as conn:
        tools = {t.name: t for t in await conn.list_tools()}
        assert set(tools) == {"target_info", "plot", "broken"}
        assert tools["target_info"].input_schema["required"] == ["symbol"]
        assert tools["target_info"].server == "ot"
        r = await conn.call_tool("target_info", {"symbol": "IL23R"})
        assert r.items == ["IL23R: ENSG00000162594"] and not r.is_error


async def test_images_and_errors_convert(fake_ot):
    async with McpConnection(CFG, target=fake_ot) as conn:
        img = await conn.call_tool("plot", {})
        assert img.items == [RemoteImage(mime="image/png", data=PNG)]
        bad = await conn.call_tool("broken", {})
        assert bad.is_error  # the SDK hides server-side exception text


def test_config_parsing_and_defaults():
    remote = ServerConfig.from_dict("ot", {"url": "https://x/mcp"})
    assert remote.clearance is Classification.PUBLIC  # remote servers default to public
    local = ServerConfig.from_dict(
        "biomcp", {"command": "biomcp", "args": ["run"], "clearance": "local"}
    )
    assert local.clearance is None and local.args == ("run",)
    with pytest.raises(ValueError):
        ServerConfig(name="x")  # neither command nor url
    with pytest.raises(ValueError):
        ServerConfig(name="bad name", url="https://x")


def test_protocol_mode_defaults():
    assert ServerConfig(name="a", command="x").mode == "legacy"
    assert ServerConfig(name="a", url="https://x").mode == "auto"
    assert ServerConfig.from_dict("a", {"url": "https://x", "protocol": "legacy"}).mode == "legacy"
