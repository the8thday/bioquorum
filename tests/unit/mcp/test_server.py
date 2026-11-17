"""The MCP exit: the registry served over MCP, every call through invoke()."""

import base64

import mcp_types
import pytest
from mcp import Client

from bioquorum.core.enums import OutputStream
from bioquorum.core.messages import UNTRUSTED_OPEN
from bioquorum.mcp import OutResult, build_server
from bioquorum.policy import PermissionPolicy
from bioquorum.provenance import ContentStore
from bioquorum.runtime import FakeKernel, KernelManager, KernelOutput, Script
from bioquorum.tools import ToolContext, default_registry
from bioquorum.tools.exits import mcp_call_fn

pytestmark = pytest.mark.anyio
PNG = b"\x89PNG\r\n\x1a\nfake"


def text(result) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, mcp_types.TextContent))


@pytest.fixture
def served(store, tmp_path):
    kernel = FakeKernel()

    async def factory(language):
        return kernel

    ctx = ToolContext(
        store=store,
        session_id="S",
        permissions=PermissionPolicy.allow_all(),
        cas=ContentStore(tmp_path / "cas"),
        kernels=KernelManager(factory),
        workdir=tmp_path,
    )
    reg = default_registry()
    return build_server(reg.definitions(), mcp_call_fn(reg, ctx)), kernel, store


async def test_lists_exactly_the_registry(served):
    server, _, _ = served
    async with Client(server) as c:
        listed = {t.name: t for t in (await c.list_tools()).tools}
    defs = {d["name"]: d for d in default_registry().definitions()}
    assert set(listed) == set(defs)
    assert listed["python_execute"].input_schema == defs["python_execute"]["input_schema"]


async def test_call_goes_through_invoke_and_is_fenced(served):
    server, kernel, store = served
    kernel.on("print", Script.stdout("n=3\n"))
    async with Client(server) as c:
        r = await c.call_tool("python_execute", {"code": "print(3)"})
    assert not r.is_error
    assert text(r).startswith(UNTRUSTED_OPEN) and "n=3" in text(r)
    types = [e.type for e in store.read("S")]
    assert types[0] == "tool_call_requested" and types[-1] == "tool_call_finished"


async def test_figures_are_returned_as_images(served):
    server, kernel, _ = served
    img = KernelOutput(
        stream=OutputStream.DISPLAY, data={"image/png": base64.b64encode(PNG).decode()}
    )
    kernel.queue(Script(outputs=[img]))
    async with Client(server) as c:
        r = await c.call_tool("python_execute", {"code": "plot()"})
    images = [b for b in r.content if isinstance(b, mcp_types.ImageContent)]
    assert len(images) == 1 and base64.b64decode(images[0].data) == PNG


async def test_unknown_tool_and_bad_args(served):
    server, _, _ = served
    async with Client(server) as c:
        assert (await c.call_tool("nope", {})).is_error
        bad = await c.call_tool("python_execute", {"code": 1})
    assert bad.is_error and "invalid arguments" in text(bad)


async def test_build_server_with_plain_callable():
    async def call(name, args):
        return OutResult(items=[f"{name}:{args['x']}"])

    defs = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    async with Client(build_server(defs, call)) as c:
        r = await c.call_tool("t", {"x": 1})
    assert text(r) == "t:1"
