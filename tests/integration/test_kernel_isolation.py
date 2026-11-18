"""Real ipykernel inside bubblewrap (ADR-017). Run with: uv run pytest -m integration"""

import asyncio
import os

import pytest

from bioquorum.runtime import EndStatus, KernelOutput
from bioquorum.runtime.sandbox import Isolation, SandboxSpec, bwrap_available

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.skipif(not bwrap_available(), reason="bubblewrap isolation not available here"),
]

OUTSIDE = os.path.abspath(__file__)  # a file that is certainly outside the project dir


@pytest.fixture
async def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".bioquorum").mkdir(parents=True)
    (root / ".bioquorum" / "project.json").write_text("{}")
    (root / "bioquorum.toml").write_text("# config\n")
    data = tmp_path / "data"
    data.mkdir()
    (data / "table.csv").write_text("a,b\n1,2\n")
    return root, data


@pytest.fixture
async def kernel(project, monkeypatch):
    from bioquorum.runtime.jupyter import JupyterKernel

    monkeypatch.setenv("FAKE_API_KEY", "sk-should-not-leak")
    root, data = project
    spec = SandboxSpec(
        workdir=root,
        read_only=(data, root / "bioquorum.toml"),
        hidden=(root / ".bioquorum",),
    )
    k = await JupyterKernel(cwd=root, sandbox=spec).start()
    yield k
    await k.shutdown()


async def out(k, code, timeout: float | None = 30) -> tuple[str, EndStatus]:
    text, status = "", EndStatus.ERROR
    async for e in k.execute(code, timeout=timeout):
        if isinstance(e, KernelOutput):
            text += e.text or e.data.get("text/plain", "")
        else:
            status = e.status
    return text.strip(), status


async def test_reports_isolation(kernel):
    assert kernel.isolation == Isolation.BWRAP


async def test_no_network(kernel):
    text, status = await out(
        kernel,
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 443), timeout=3); print('CONNECTED')\n"
        "except OSError as e:\n"
        "    print('blocked')",
    )
    assert (text, status) == ("blocked", EndStatus.OK)


async def test_cannot_see_files_outside_project(kernel):
    text, _ = await out(kernel, f"import os; print(os.path.exists({OUTSIDE!r}))")
    assert text == "False"


async def test_project_writable_data_read_only(kernel, project):
    root, data = project
    text, status = await out(
        kernel,
        "open('result.txt', 'w').write('ok')\n"
        f"print(open({str(data / 'table.csv')!r}).read().splitlines()[0])\n"
        "try:\n"
        f"    open({str(data / 'new.csv')!r}, 'w').write('x')\n"
        "except OSError:\n"
        "    print('data is read-only')",
    )
    assert status is EndStatus.OK and text.splitlines() == ["a,b", "data is read-only"]
    assert (root / "result.txt").read_text() == "ok"
    assert not (data / "new.csv").exists()


async def test_state_dir_hidden_and_config_read_only(kernel, project):
    root, _ = project
    text, _ = await out(
        kernel,
        "import os\n"
        "print(os.listdir('.bioquorum'))\n"
        "try:\n"
        "    open('bioquorum.toml', 'a').write('[mcp.servers.evil]')\n"
        "except OSError:\n"
        "    print('config is read-only')",
    )
    assert text.splitlines() == ["[]", "config is read-only"]
    assert (root / "bioquorum.toml").read_text() == "# config\n"
    assert (root / ".bioquorum" / "project.json").exists()


async def test_environment_has_no_secrets(kernel):
    text, _ = await out(kernel, "import os; print('FAKE_API_KEY' in os.environ)")
    assert text == "False"


async def test_interrupt_and_timeout_keep_the_kernel_alive(kernel):
    task = asyncio.create_task(out(kernel, "import time; time.sleep(30)", None))
    await asyncio.sleep(1)
    await kernel.interrupt()
    assert (await task)[1] is EndStatus.CANCELLED
    assert (await out(kernel, "import time; time.sleep(30)", 1))[1] is EndStatus.TIMED_OUT
    assert await out(kernel, "print(6 * 7)") == ("42", EndStatus.OK)


async def test_scientific_stack_still_works(kernel, project):
    root, _ = project
    text, status = await out(
        kernel,
        "import pandas as pd\n"
        "df = pd.DataFrame({'x': [1, 2, 3]}); df.to_parquet('t.parquet')\n"
        "print(pd.read_parquet('t.parquet').x.sum())",
        60,
    )
    assert (text, status) == ("6", EndStatus.OK)
    assert (root / "t.parquet").exists()


async def test_bq_prelude_works_in_the_sandbox(kernel):
    evs = [e async for e in kernel.execute("bq.value('n', 3, unit='genes')", timeout=30)]
    assert evs[-1].status is EndStatus.OK
    assert any(getattr(e, "data", {}).get("text/plain") == "bq.value n = 3 genes" for e in evs)
