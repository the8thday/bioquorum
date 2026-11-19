"""Exercise the actual macOS process boundary with an installed Python and MCP SDK."""

import asyncio
import sys

import pytest

from bioquorum.application import workspace
from bioquorum.core.enums import Classification
from bioquorum.mcp.client import ServerConfig
from bioquorum.runtime.kernel import KernelOutput
from bioquorum.runtime.manager import jupyter_factory
from bioquorum.runtime.sandbox import Isolation, seatbelt_available

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def macos_only():
    if sys.platform != "darwin" or not seatbelt_available():
        pytest.skip("Requires the native macOS seatbelt facility")


async def test_macos_jupyter_can_use_private_ipc_and_isolates_data(tmp_path):
    macos_only()
    root = tmp_path / "project"
    ws = workspace.init_project(root, classification=Classification.CONFIDENTIAL)
    ws.config = {"kernel": {"macos_sandbox": True}}
    outside = tmp_path / "unrelated.txt"
    outside.write_text("outside")
    state = ws.state_dir / "sentinel"
    state.write_text("state")
    assert ws.kernel_isolation() is Isolation.SEATBELT

    async def accept(_reader, writer):
        writer.close()

    listener = await asyncio.start_server(accept, "127.0.0.1", 0)
    code = f"""import socket
from pathlib import Path
def check(f):
    try:
        f()
    except PermissionError:
        return 'denied'
    return 'allowed'
print(check(lambda: Path({str(outside)!r}).read_text()))
print(check(lambda: Path({str(state)!r}).read_text()))
print(check(lambda: Path('bioquorum.toml').write_text('overwrite')))
print(check(lambda: socket.create_connection(('127.0.0.1', {listener.sockets[0].getsockname()[1]}), 2)))
print(check(lambda: Path('generated.txt').write_text('ok')))
"""
    async with listener:
        kernel = await jupyter_factory(root, sandbox=ws.kernel_sandbox(), require_isolation=True)(
            "python"
        )
        try:
            outputs = [
                event.text
                for event in [e async for e in kernel.execute(code)]
                if isinstance(event, KernelOutput)
            ]
        finally:
            await kernel.shutdown()
    assert "".join(s for s in outputs if s).split() == ["denied"] * 4 + ["allowed"]
    assert (root / "generated.txt").read_text() == "ok"


async def test_offline_stdio_mcp_has_no_project_or_internet_access(tmp_path):
    macos_only()
    ws = workspace.init_project(tmp_path / "project", classification=Classification.PUBLIC)
    private = ws.root / "project-data.txt"
    private.write_text("secret")

    async def accept(_reader, writer):
        writer.close()

    listener = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    program = tmp_path / "connector.py"
    program.write_text(f"""import socket
from pathlib import Path
from mcp.server.mcpserver import MCPServer
server = MCPServer('offline-probe')
@server.tool()
def inspect_scope() -> str:
    project_denied = False
    ip_denied = False
    try:
        Path({str(private)!r}).read_text()
    except PermissionError:
        project_denied = True
    try:
        socket.create_connection(('127.0.0.1', {port}), 2).close()
    except PermissionError:
        ip_denied = True
    return f'project_denied={{project_denied}}, ip_denied={{ip_denied}}'
if __name__ == '__main__':
    server.run(transport='stdio')
""")
    cfg = ServerConfig(
        name="probe",
        command=sys.executable,
        args=(str(program),),
        isolated=True,
        read_paths=(str(program),),
        timeout_s=15,
    )
    async with listener, ws.mcp_connection(cfg) as connection:
        reply = await connection.call_tool("inspect_scope", {})
    assert not reply.is_error and "project_denied=True, ip_denied=True" in reply.text()


async def test_public_opt_in_replays_in_fresh_seatbelt_kernel(tmp_path):
    from bioquorum.application import verify
    from bioquorum.application.controls import ResearchControls
    from bioquorum.core import events as ev
    from bioquorum.core.enums import Actor, ClaimKind
    from bioquorum.core.refs import ValueRef
    from bioquorum.core.status import StatusTrigger, VerificationStatus
    from bioquorum.policy.permissions import PermissionPolicy
    from bioquorum.tools.builtin.kernel import PYTHON_EXECUTE
    from bioquorum.tools.invoke import invoke

    macos_only()
    ws = workspace.init_project(tmp_path, classification=Classification.PUBLIC)
    ws.config = {"kernel": {"macos_sandbox": True}}
    controls = ResearchControls(ws)
    sid = controls.new_session("Replay")
    ctx = workspace.tool_context(ws, controls.store, sid, PermissionPolicy.allow_all())
    try:
        result = await invoke(PYTHON_EXECUTE, {"code": "bq.value('n', 3)"}, ctx)
        assert not result.is_error, result.text()
    finally:
        await ctx.require_kernels().shutdown_all()
    (start,) = [e.parsed() for e in controls.store.read(sid) if e.type == "run_started"]
    assert isinstance(start, ev.RunStarted)
    controls.store.append(
        ev.ClaimExtracted(
            claim_id="count",
            source_event_id="answer",
            text="{{n}}",
            kind=ClaimKind.NUMERIC,
            refs=[ValueRef(run_id=start.run_id, name="n")],
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    controls.store.append(
        ev.ClaimStatusChanged(
            claim_id="count",
            from_status=VerificationStatus.GROUNDED,
            to_status=VerificationStatus.REVIEWED,
            trigger=StatusTrigger.REVIEW_PASSED,
        ),
        session_id=sid,
        actor=Actor.REVIEWER,
    )
    outcome = await verify.rerun(controls, sid, "count")
    assert outcome["outcome"] == "matched", outcome
    assert not outcome["status_eligible"]
    assert [
        e.payload["isolation"] for e in controls.store.read(sid) if e.type == "run_started"
    ] == ["seatbelt", "seatbelt"]
