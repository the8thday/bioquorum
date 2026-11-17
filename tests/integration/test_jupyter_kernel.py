"""Real ipykernel. Run with: uv run pytest -m integration"""

import asyncio

import pytest

from bioquorum.core.enums import OutputStream
from bioquorum.runtime import EndStatus, ExecutionEnd, KernelOutput

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def kernel():
    from bioquorum.runtime.jupyter import JupyterKernel

    k = await JupyterKernel().start()
    yield k
    await k.shutdown()


async def run(k, code, timeout: float | None = 30):
    return [e async for e in k.execute(code, timeout=timeout)]


async def test_stdout_result_and_state(kernel):
    await run(kernel, "x = 41")
    evs = await run(kernel, "print('hi'); x + 1")
    outs = [e for e in evs if isinstance(e, KernelOutput)]
    assert outs[0].stream is OutputStream.STDOUT and outs[0].text == "hi\n"
    assert outs[1].data["text/plain"] == "42"
    assert evs[-1] == ExecutionEnd(status=EndStatus.OK)


async def test_error(kernel):
    end = (await run(kernel, "1/0"))[-1]
    assert end.status is EndStatus.ERROR and end.error.ename == "ZeroDivisionError"


async def test_timeout_interrupts_and_kernel_survives(kernel):
    end = (await run(kernel, "import time; time.sleep(30)", timeout=1))[-1]
    assert end.status is EndStatus.TIMED_OUT
    assert (await run(kernel, "print(1)"))[-1].status is EndStatus.OK


async def test_interrupt_cancels(kernel):
    task = asyncio.create_task(run(kernel, "import time; time.sleep(30)", timeout=None))
    await asyncio.sleep(1)
    await kernel.interrupt()
    assert (await task)[-1].status is EndStatus.CANCELLED


async def test_snapshot_variables_hashes_dataframes_like_provenance(kernel):
    import pandas as pd

    import bioquorum.provenance  # noqa: F401
    from bioquorum.core.hashing import hash_object

    await run(
        kernel, "import pandas as pd\ndf = pd.DataFrame({'b': [1, 2], 'a': [0.5, 1.5]})\ns = 'abc'"
    )
    snap = await kernel.snapshot_variables()
    local = pd.DataFrame({"b": [1, 2], "a": [0.5, 1.5]})
    assert snap["df"].content_hash == hash_object(local).hash
    assert snap["s"].content_hash == hash_object("abc").hash
