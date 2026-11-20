"""Exercise the real Linux bubblewrap boundary around an offline stdio MCP server."""

import asyncio
import sys
from pathlib import Path

import pytest

from bioquorum.application import workspace
from bioquorum.core.enums import Classification
from bioquorum.mcp.client import ServerConfig
from bioquorum.runtime.sandbox import bwrap_available

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def linux_only():
    if not sys.platform.startswith("linux") or not bwrap_available():
        pytest.skip("Requires Linux with usable unprivileged bubblewrap")


async def test_linux_offline_stdio_mcp_sees_no_project_network_or_secrets(tmp_path, monkeypatch):
    linux_only()
    monkeypatch.setenv("BQ_TEST_SECRET", "must-not-leak")
    ws = workspace.init_project(tmp_path / "project", classification=Classification.CONFIDENTIAL)
    private = ws.root / "project-data.txt"
    private.write_text("secret")
    home_secret = Path.home() / ".ssh"

    async def accept(_reader, writer):
        writer.close()

    listener = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    program = tmp_path / "connector.py"
    program.write_text(f"""import os, socket
from pathlib import Path
from mcp.server.mcpserver import MCPServer
server = MCPServer('offline-probe')
def blocked(f):
    try:
        f()
    except OSError:
        return True
    return False
@server.tool()
def inspect_scope() -> str:
    checks = {{
        'project': blocked(lambda: Path({str(private)!r}).read_text()),
        'ssh': blocked(lambda: os.listdir({str(home_secret)!r})),
        'loopback': blocked(lambda: socket.create_connection(('127.0.0.1', {port}), 2).close()),
        'internet': blocked(lambda: socket.create_connection(('1.1.1.1', 443), 3).close()),
        'secret_env': 'BQ_TEST_SECRET' not in os.environ,
        'workdir_writable': not blocked(lambda: Path('scratch.txt').write_text('ok')),
    }}
    return ' '.join(f'{{k}}={{v}}' for k, v in checks.items())
if __name__ == '__main__':
    server.run(transport='stdio')
""")
    cfg = ServerConfig(
        name="probe",
        command=sys.executable,
        args=(str(program),),
        isolated=True,
        read_paths=(str(program),),
        timeout_s=20,
    )
    async with listener, ws.mcp_connection(cfg) as connection:
        reply = await connection.call_tool("inspect_scope", {})
    assert not reply.is_error
    assert reply.text().split() == [
        "project=True",
        "ssh=True",
        "loopback=True",
        "internet=True",
        "secret_env=True",
        "workdir_writable=True",
    ]
