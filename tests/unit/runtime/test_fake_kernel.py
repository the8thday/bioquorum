import asyncio

import pytest

from bioquorum.core.enums import OutputStream
from bioquorum.runtime import EndStatus, ExecutionEnd, FakeKernel, Kernel, KernelManager, Script

pytestmark = pytest.mark.anyio


async def collect(kernel, code, timeout=None):
    return [e async for e in kernel.execute(code, timeout=timeout)]


def test_fake_kernel_satisfies_protocol():
    assert isinstance(FakeKernel(), Kernel)


async def test_stream_always_ends_with_exactly_one_end():
    k = FakeKernel().on("print", Script.stdout("hi\n"))
    events = await collect(k, "print('hi')")
    assert events[0].stream is OutputStream.STDOUT and events[0].text == "hi\n"
    assert isinstance(events[-1], ExecutionEnd) and events[-1].status is EndStatus.OK
    assert sum(isinstance(e, ExecutionEnd) for e in events) == 1


async def test_error_script():
    k = FakeKernel().queue(Script.fail("ValueError", "bad"))
    end = (await collect(k, "x"))[-1]
    assert end.status is EndStatus.ERROR and end.error.short() == "ValueError: bad"


async def test_unmatched_code_succeeds_quietly():
    events = await collect(FakeKernel(), "1 + 1")
    assert [type(e) for e in events] == [ExecutionEnd]


async def test_hang_times_out():
    k = FakeKernel().queue(Script(hang=True))
    end = (await collect(k, "while True: pass", timeout=0.05))[-1]
    assert end.status is EndStatus.TIMED_OUT and end.timeout_s == 0.05


async def test_hang_interrupted_is_cancelled():
    k = FakeKernel().queue(Script(hang=True))
    task = asyncio.create_task(collect(k, "sleep", timeout=5))
    await asyncio.sleep(0.01)
    await k.interrupt()
    assert (await task)[-1].status is EndStatus.CANCELLED


async def test_manager_reuses_kernel_per_session_and_language():
    made = []

    async def factory(language):
        k = FakeKernel(language=language)
        made.append(k)
        return k

    m = KernelManager(factory)
    a = await m.get("S1")
    assert await m.get("S1") is a
    assert await m.get("S2") is not a
    assert (await m.get("S1", "r")).language == "r"
    assert m.lock("S1") is m.lock("S1")
    await m.shutdown("S1")
    assert sorted(m.active()) == [("S2", "python")]
    await m.shutdown_all()
    assert all(k.closed for k in made)


@pytest.mark.anyio
async def test_r_kernel_never_falls_back_to_user_kernelspec(tmp_path):
    from bioquorum.runtime.manager import jupyter_factory

    with pytest.raises(ValueError, match="pixi"):
        await jupyter_factory(tmp_path)("r")
