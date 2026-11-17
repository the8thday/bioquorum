import pytest
from pydantic import BaseModel

from bioquorum.core.enums import PermissionClass, Trust
from bioquorum.tools import ToolRegistry, ToolResult, ToolSpec, default_registry


class Args(BaseModel):
    q: str


async def handler(args, ctx):
    return ToolResult.text(args.q)


def spec(name="t", **kw):
    base = {"permission": PermissionClass.READ_FS, "args_model": Args}
    return ToolSpec(name=name, description="d", handler=handler, **{**base, **kw})


def test_duplicate_names_rejected():
    reg = ToolRegistry([spec()])
    with pytest.raises(ValueError):
        reg.register(spec())


def test_definitions_are_sorted_and_have_schema():
    reg = ToolRegistry([spec("b"), spec("a")])
    defs = reg.definitions()
    assert [d["name"] for d in defs] == ["a", "b"]
    assert defs[0]["input_schema"]["properties"]["q"]["type"] == "string"


def test_exactly_one_schema_source():
    with pytest.raises(ValueError):
        spec(raw_input_schema={"type": "object"})
    with pytest.raises(ValueError):
        ToolSpec(name="x", description="d", handler=handler, permission=PermissionClass.READ_FS)


@pytest.mark.parametrize(
    "perm", [PermissionClass.NETWORK, PermissionClass.CONNECTOR, PermissionClass.EXTERNAL_MCP]
)
def test_remote_output_can_never_be_trusted(perm):
    with pytest.raises(ValueError):
        spec(permission=perm, output_trust=Trust.TRUSTED)


def test_builtin_registry():
    reg = default_registry()
    assert {"python_execute", "read_file", "list_files"} <= {s.name for s in reg}
    assert all(s.output_trust is Trust.UNTRUSTED for s in reg)


def test_unknown_tool_raises_keyerror():
    with pytest.raises(KeyError):
        ToolRegistry().get("nope")
