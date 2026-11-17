"""External MCP tools wrapped as ToolSpecs and run through invoke()."""

import pytest

from bioquorum.core.enums import Classification, PermissionClass, PermissionScope, Trust
from bioquorum.core.messages import ImageRefPart
from bioquorum.mcp import McpConnection, ServerConfig
from bioquorum.policy import PermissionPolicy
from bioquorum.tools import ToolRegistry, invoke
from bioquorum.tools.external import qualified_name, register_external
from tests.unit.mcp.conftest import PNG, fake_ot  # noqa: F401  (fixture)

pytestmark = pytest.mark.anyio


def cfg(clearance: Classification | None = Classification.PUBLIC) -> ServerConfig:
    return ServerConfig(name="ot", url="https://example.invalid/mcp", clearance=clearance)


async def test_wrapped_specs_are_untrusted_external(fake_ot):  # noqa: F811
    reg = ToolRegistry()
    async with McpConnection(cfg(), target=fake_ot) as conn:
        specs = await register_external(reg, conn)
    assert {s.name for s in specs} == {"ot__target_info", "ot__plot", "ot__broken"}
    for s in specs:
        assert s.permission is PermissionClass.EXTERNAL_MCP
        assert s.output_trust is Trust.UNTRUSTED
        assert s.classification_floor is Classification.PUBLIC
        assert s.description.startswith("[external MCP server 'ot']")


async def test_call_through_invoke(fake_ot, ctx, store):  # noqa: F811
    ctx.classification = Classification.PUBLIC
    reg = ToolRegistry()
    async with McpConnection(cfg(), target=fake_ot) as conn:
        await register_external(reg, conn)
        part = await invoke(reg.get("ot__target_info"), {"symbol": "IL23R"}, ctx)
        img = await invoke(reg.get("ot__plot"), {}, ctx)
        bad = await invoke(reg.get("ot__broken"), {}, ctx)
    assert part.text() == "IL23R: ENSG00000162594" and part.trust is Trust.UNTRUSTED
    (ref,) = [p for p in img.content if isinstance(p, ImageRefPart)]
    assert ctx.cas.get(ref.ref) == PNG
    assert bad.is_error


async def test_internal_project_cannot_reach_public_server(fake_ot, ctx, store):  # noqa: F811
    ctx.classification = Classification.INTERNAL
    reg = ToolRegistry()
    async with McpConnection(cfg(), target=fake_ot) as conn:
        await register_external(reg, conn)
        part = await invoke(reg.get("ot__target_info"), {"symbol": "IL23R"}, ctx)
    assert part.is_error and "classification" in part.text()
    assert [e.type for e in store.read("S")] == ["error", "tool_call_failed"]


async def test_local_server_allowed_for_confidential(fake_ot, ctx):  # noqa: F811
    ctx.classification = Classification.CONFIDENTIAL
    reg = ToolRegistry()
    async with McpConnection(cfg(clearance=None), target=fake_ot) as conn:
        await register_external(reg, conn)
        part = await invoke(reg.get("ot__target_info"), {"symbol": "X"}, ctx)
    assert not part.is_error


async def test_permission_is_per_server(fake_ot, ctx):  # noqa: F811
    ctx.classification = Classification.PUBLIC
    ctx.permissions = PermissionPolicy()
    ctx.permissions.grant(PermissionClass.EXTERNAL_MCP, "mcp:other", PermissionScope.GLOBAL)
    reg = ToolRegistry()
    async with McpConnection(cfg(), target=fake_ot) as conn:
        await register_external(reg, conn)
        part = await invoke(reg.get("ot__target_info"), {"symbol": "X"}, ctx)
    assert part.is_error and "denied for mcp:ot" in part.text()


def test_qualified_name_is_capped():
    assert len(qualified_name("s" * 40, "t" * 40)) == 64
