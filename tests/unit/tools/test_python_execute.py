import base64

import pytest

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, OutputStream
from bioquorum.core.hashing import code_hash, run_key
from bioquorum.core.messages import DataRefPart, ImageRefPart, ToolResultPart
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


async def test_declared_download_is_hashed_and_checked_before_run(ctx, store, kernel):
    path = ctx.workdir / "downloads" / "table.csv"
    path.parent.mkdir()
    path.write_text("observed\n2\n")
    assert ctx.cas is not None
    ref = ctx.cas.put_file(path)
    store.append(
        ev.ToolCallFinished(
            call_id="download",
            tool="download_file",
            result=ToolResultPart(
                call_id="download",
                content=[
                    DataRefPart(ref=ref, summary="downloads/table.csv, downloaded from example")
                ],
            ),
        ),
        session_id="S",
        actor=Actor.TOOL,
    )
    args = {"code": "pass", "input_files": ["downloads/table.csv"]}
    assert not (await invoke(SPEC, args, ctx)).is_error
    started, finished = (e.parsed() for e in run_events(store) if e.type != "run_output")
    assert isinstance(started, ev.RunStarted) and started.inputs == [ref]
    assert isinstance(finished, ev.RunFinished)
    assert finished.run_key == run_key(code_hash("pass"), None, [ref.content_hash])
    path.write_text("mutated")
    assert (await invoke(SPEC, args, ctx)).is_error
    assert len([e for e in run_events(store) if e.type == "run_started"]) == 1


async def test_local_data_input_requires_read_permission(ctx, store):
    from bioquorum.core.enums import PermissionClass, PermissionScope
    from bioquorum.policy import PermissionPolicy

    ctx.permissions = PermissionPolicy()
    ctx.permissions.grant(PermissionClass.EXECUTE, "python", PermissionScope.GLOBAL)
    path = ctx.workdir / "public.csv"
    path.write_text("counts\n2\n")
    args = {"code": "pass", "input_files": ["public.csv"]}
    assert (await invoke(SPEC, args, ctx)).is_error
    ctx.permissions.grant(PermissionClass.READ_FS, str(path), PermissionScope.GLOBAL)
    assert not (await invoke(SPEC, args, ctx)).is_error
    started = run_events(store)[0].parsed()
    assert isinstance(started, ev.RunStarted) and len(started.inputs) == 1
    (ctx.workdir / ".env").write_text("sample")
    ctx.permissions.grant(PermissionClass.READ_FS, str(ctx.workdir), PermissionScope.GLOBAL)
    assert (await invoke(SPEC, {"code": "pass", "input_files": [".env"]}, ctx)).is_error


async def test_bilingual_tools_use_separate_persistent_kernels(ctx, store):
    from bioquorum.review.evidence import Evidence
    from bioquorum.runtime import FakeKernel, KernelManager

    async def create(language):
        kernel = FakeKernel(language=language)
        kernel.on("declare", Script(outputs=[value_output("n_" + language, 7)]))
        return kernel

    ctx.kernels = KernelManager(create)
    for language in ("r", "python", "r"):
        spec = default_registry().get(language + "_execute")
        part = await invoke(spec, {"code": "declare"}, ctx)
        assert not part.is_error
    starts = [e.parsed() for e in store.read("S") if e.type == "run_started"]
    assert [s.language for s in starts] == ["r", "python", "r"]
    assert starts[0].kernel_id == starts[2].kernel_id != starts[1].kernel_id
    assert not Evidence.from_events(store.read("S")).sources


async def test_cross_language_name_collision_cannot_bind(ctx, store, kernel):
    from bioquorum.review.evidence import Evidence

    kernel.queue(*(Script(outputs=[value_output("n", value)]) for value in (1, 2)))
    for language in ("python", "r"):
        await invoke(default_registry().get(language + "_execute"), {"code": "declare"}, ctx)
    assert "n" not in Evidence.from_events(store.read("S")).values


async def test_cross_language_name_collision_persists_across_system_binding_retry(
    ctx, store, kernel
):
    from bioquorum.core.messages import Message
    from bioquorum.review.evidence import Evidence

    kernel.queue(*(Script(outputs=[value_output("n", value)]) for value in (1, 2, 3)))
    await invoke(default_registry().get("python_execute"), {"code": "declare"}, ctx)
    store.append(
        ev.UserMessage(message=Message.user("Fix unresolved bindings.")),
        session_id="S",
        actor=Actor.SYSTEM,
    )
    await invoke(default_registry().get("r_execute"), {"code": "declare"}, ctx)
    assert "n" not in Evidence.from_events(store.read("S")).values
    store.append(
        ev.UserMessage(message=Message.user("Next user turn.")),
        session_id="S",
        actor=Actor.USER,
    )
    await invoke(default_registry().get("r_execute"), {"code": "declare"}, ctx)
    assert Evidence.from_events(store.read("S")).values["n"].value.value == 3


async def test_declared_output_path_is_normalized_for_handoff_and_dedup(ctx, store):
    import json

    from bioquorum.runtime import FakeKernel, KernelManager
    from bioquorum.runtime.lineage import LINEAGE_MIME
    from bioquorum.tools.replay import dependency_chain

    path = ctx.workdir / "outputs" / "counts.csv"

    def write_counts(code):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("gene,count\na,7\n")
        return True

    observed = KernelOutput(
        stream=OutputStream.DISPLAY,
        data={
            LINEAGE_MIME: json.dumps(
                {"path": str(path), "direction": "output", "function": "pandas.DataFrame.to_csv"}
            )
        },
    )

    async def make(language):
        kernel = FakeKernel(language=language)
        if language == "python":
            kernel.on(write_counts, Script(outputs=[observed]))
        return kernel

    ctx.kernels = KernelManager(make)
    await invoke(SPEC, {"code": "make", "output_files": ["./outputs//counts.csv"]}, ctx)
    await invoke(
        default_registry().get("r_execute"),
        {"code": "read", "input_files": ["outputs/counts.csv"]},
        ctx,
    )
    events = store.read("S")
    starts = [e.parsed() for e in events if e.type == "run_started"]
    writes = [e.parsed() for e in events if e.type == "lineage_observed"]
    assert len(writes) == 1
    assert writes[0].path == "outputs/counts.csv"
    assert writes[0].function == "declared_write"
    assert dependency_chain(events, starts[1]) == starts


async def test_cross_language_handoff_records_matching_bytes(ctx, store):
    from bioquorum.runtime import FakeKernel, KernelManager
    from bioquorum.tools.replay import dependency_chain

    path = ctx.workdir / "counts.csv"

    def write_counts(code):
        path.write_text("gene,count\na,7\n")
        return True

    async def make(language):
        kernel = FakeKernel(language=language)
        if language == "python":
            kernel.on(write_counts, Script())
        return kernel

    ctx.kernels = KernelManager(make)
    await invoke(SPEC, {"code": "make", "output_files": ["counts.csv"]}, ctx)
    await invoke(
        default_registry().get("r_execute"), {"code": "read", "input_files": ["counts.csv"]}, ctx
    )
    events = store.read("S")
    starts = [e.parsed() for e in events if e.type == "run_started"]
    writes = [e.parsed() for e in events if e.type == "lineage_observed"]
    assert writes[0].post_run_hash == starts[1].inputs[0].content_hash
    assert ctx.cas.get_bytes(writes[0].post_run_hash) == path.read_bytes()
    assert dependency_chain(events, starts[1]) == starts


async def test_declared_output_needs_new_bytes_or_updated_file(ctx, store, kernel):
    from bioquorum.tools.replay import recorded_writes

    (ctx.workdir / "old.csv").write_text("old")
    kernel.queue(Script(outputs=[value_output("count", 5)]))
    part = await invoke(SPEC, {"code": "pass", "output_files": ["old.csv"]}, ctx)
    assert part.is_error
    assert not any(e.type == "run_finished" for e in store.read("S"))
    assert not recorded_writes(store.read("S"))
