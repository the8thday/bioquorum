import base64

import pytest

from bioquorum.core.enums import OutputStream
from bioquorum.core.hashing import code_hash, run_key
from bioquorum.core.messages import ImageRefPart
from bioquorum.core.refs import NamedValue
from bioquorum.runtime import KernelError, KernelOutput, Script
from bioquorum.runtime.values import VALUE_MIME, value_output
from bioquorum.storage.rebuild import snapshot
from bioquorum.tools import default_registry, invoke

pytestmark = pytest.mark.anyio

PNG = b"\x89PNG\r\n\x1a\nfake"
SPEC = default_registry().get("python_execute")


def run_events(store):
    return [e for e in store.read("S") if e.type.startswith("run_")]


async def test_successful_run_is_recorded(ctx, store, kernel):
    kernel.on("print", Script.stdout("n=3\n"))
    code = "print('n=3')"
    part = await invoke(SPEC, {"code": code}, ctx)
    assert not part.is_error
    text = part.text()
    evs = run_events(store)
    assert [e.type for e in evs] == ["run_started", "run_output", "run_finished"]
    run_id = evs[0].payload["run_id"]
    assert f"run_id: {run_id}" in text and "n=3" in text
    assert evs[0].payload["code_hash"] == code_hash(code)
    assert evs[0].payload["kernel_id"] == "K"
    finished = evs[-1].parsed()
    assert finished.run_key == run_key(code_hash(code), None, [])
    assert ctx.cas.get(finished.stdout_ref) == b"n=3\n"
    (row,) = snapshot(store.db)["runs"]
    assert row["status"] == "succeeded" and row["run_key"] == finished.run_key
    # causation chain: tool_call_requested -> run_started -> run_*
    requested = store.read("S")[0]
    assert requested.type == "tool_call_requested" and evs[0].causation_id == requested.id
    assert all(e.causation_id == evs[0].id for e in evs[1:])


async def test_figures_go_to_cas_and_come_back_as_refs(ctx, store, kernel):
    img = KernelOutput(
        stream=OutputStream.DISPLAY,
        data={"image/png": base64.b64encode(PNG).decode(), "text/plain": "<Figure>"},
    )
    kernel.queue(Script(outputs=[img]))
    part = await invoke(SPEC, {"code": "plt.show()"}, ctx)
    refs = [p for p in part.content if isinstance(p, ImageRefPart)]
    assert len(refs) == 1 and ctx.cas.get(refs[0].ref) == PNG
    assert "base64" not in part.text() and "figure 1" in part.text()
    finished = run_events(store)[-1].parsed()
    assert finished.outputs == [refs[0].ref]


async def test_error_run(ctx, store, kernel):
    kernel.queue(Script.fail("KeyError", "'gene'"))
    part = await invoke(SPEC, {"code": "df['gene']"}, ctx)
    assert part.is_error and "KeyError: 'gene'" in part.text()
    assert run_events(store)[-1].type == "run_failed"
    # the tool call itself completed: the failure is in the run, not in invoke
    assert store.read("S")[-1].type == "tool_call_finished"
    assert snapshot(store.db)["runs"][0]["status"] == "failed"


async def test_timeout_ends_run(ctx, store, kernel):
    kernel.queue(Script(hang=True))
    part = await invoke(SPEC, {"code": "while True: pass", "timeout_s": 0.05}, ctx)
    assert part.is_error and "timed out" in part.text()
    assert run_events(store)[-1].type == "run_timed_out"
    assert snapshot(store.db)["runs"][0]["status"] == "timed_out"


async def test_kernel_crash_still_ends_run(ctx, store, kernel):
    async def broken(code, *, timeout=None):
        raise ConnectionError("kernel died")
        yield  # pragma: no cover

    kernel.execute = broken
    part = await invoke(SPEC, {"code": "x"}, ctx)
    assert part.is_error and "kernel died" in part.text()
    assert run_events(store)[-1].type == "run_failed"


async def test_long_output_truncated_for_model(ctx, store, kernel):
    kernel.queue(Script.stdout("x" * 50_000))
    part = await invoke(SPEC, {"code": "print(big)"}, ctx)
    assert len(part.text()) < 5_000 and "truncated" in part.text()
    finished = run_events(store)[-1].parsed()
    assert len(ctx.cas.get(finished.stdout_ref)) == 50_000  # full copy kept


# ----------------------------------------------------------------- bq.value (ADR-014)


async def test_declared_values_are_recorded_on_run_finished(ctx, store, kernel):
    kernel.queue(
        Script(
            outputs=[
                value_output("n_de", 412, unit="genes"),
                value_output("p_min", 1e-9),
                value_output("n_de", 413, unit="genes"),  # same name: last one wins
            ]
        )
    )
    part = await invoke(SPEC, {"code": "bq.value('n_de', 412)"}, ctx)
    assert "bq.value n_de = 413 genes" in part.text()  # the model sees what it declared
    finished = run_events(store)[-1].parsed()
    assert finished.values == [
        NamedValue(name="n_de", value=413, unit="genes"),
        NamedValue(name="p_min", value=1e-9),
    ]


async def test_values_of_a_failed_run_are_not_recorded(ctx, store, kernel):
    kernel.queue(
        Script(outputs=[value_output("n", 3)], error=KernelError(ename="E", evalue="boom"))
    )
    part = await invoke(SPEC, {"code": "bq.value('n', 3); 1/0"}, ctx)
    assert run_events(store)[-1].type == "run_failed"
    assert "not recorded" in part.text() and "n" in part.text()


async def test_malformed_value_payload_is_reported_not_recorded(ctx, store, kernel):
    bad = KernelOutput(stream=OutputStream.DISPLAY, data={VALUE_MIME: '{"name": "1x", "value": 1}'})
    kernel.queue(Script(outputs=[bad]))
    part = await invoke(SPEC, {"code": "..."}, ctx)
    assert run_events(store)[-1].parsed().values == []
    assert "rejected" in part.text()
