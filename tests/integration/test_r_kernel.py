"""Exercise the actual project IRkernel and host isolation when provisioned."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from bioquorum.runtime import EndStatus, ExecutionEnd, KernelOutput
from bioquorum.runtime.manager import jupyter_factory
from bioquorum.runtime.sandbox import SandboxSpec
from bioquorum.runtime.values import parse_value

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_project_ir_kernel_isolation_recovery_and_values(tmp_path):
    project = Path(os.environ.get("BQ_R_PROJECT", "examples/v09")).resolve()
    python = project / ".pixi/envs/default/bin/python"
    if not python.is_file():
        pytest.skip("Provision examples/v09 with pixi or set BQ_R_PROJECT")
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    root = tmp_path / "project"
    root.mkdir()
    kernel = await jupyter_factory(
        root,
        python_executable=python,
        require_isolation=True,
        sandbox=SandboxSpec(root, macos=sys.platform == "darwin"),
    )("r")

    async def run(code, timeout=15):
        return [item async for item in kernel.execute(code, timeout=timeout)]

    try:
        code = """
        stopifnot(!file.exists("../private.txt"), !nzchar(Sys.getenv("OPENAI_API_KEY")))
        conn <- try(socketConnection("127.0.0.1", port=port, open="r+", timeout=1), silent=TRUE)
        stopifnot(inherits(conn, "try-error"))
        x <- 7L
        """
        listener = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        async with listener:
            port = listener.sockets[0].getsockname()[1]
            end = (await run(f"port <- {port}L\n" + code))[-1]
            assert isinstance(end, ExecutionEnd) and end.status == EndStatus.OK
        values = [
            parse_value(v)
            for v in await run('bq_value("n", x, "cells")')
            if isinstance(v, KernelOutput)
        ]
        assert any(getattr(v, "value", None) == 7 for v in values)
        import base64

        graphics = await run("plot(1:3); print(bq_summary(data.frame(x=1:10))$rows)")
        assert any(
            isinstance(item, KernelOutput)
            and "image/png" in item.data
            and base64.b64decode(item.data["image/png"]).startswith(b"\x89PNG\r\n\x1a\n")
            for item in graphics
        )
        assert any(
            isinstance(item, KernelOutput) and "10" in (item.text or "") for item in graphics
        )
        for manual in (False, True):
            old = kernel.kernel_id
            task = asyncio.create_task(run("Sys.sleep(60)", 60 if manual else 1))
            if manual:
                await asyncio.sleep(0.5)
                await kernel.interrupt()
            end = (await asyncio.wait_for(task, 75))[-1]
            assert isinstance(end, ExecutionEnd)
            assert end.status == (EndStatus.CANCELLED if manual else EndStatus.TIMED_OUT)
            assert kernel.kernel_id != old
            end = (await run('bq_value("ok", 1L)'))[-1]
            assert isinstance(end, ExecutionEnd) and end.status == EndStatus.OK
    finally:
        await kernel.shutdown()


def test_r_value_wire_payload_with_local_r(tmp_path):
    """Capture the IRdisplay callback in base R; this does not start an IRkernel."""
    import json
    import shutil
    import subprocess

    from bioquorum.core.enums import OutputStream
    from bioquorum.core.refs import NamedValue

    if not (r := shutil.which("Rscript")):
        pytest.skip("Rscript unavailable")
    prelude = Path("src/bioquorum/runtime/r_prelude.R").resolve()
    script = """
    if (!requireNamespace("jsonlite", quietly = TRUE)) quit(status=77)
    source(commandArgs(trailingOnly=TRUE)[1])
    options(jupyter.base_display_func=function(data, metadata) {
        cat(data[["application/vnd.bioquorum.value+json"]])
    })
    stopifnot(nrow(bq_summary(data.frame(x=1:20))$head) == 5L)
    stopifnot(inherits(try(bq_value("invalid name", 1), silent=TRUE), "try-error"))
    stopifnot(inherits(try(bq_value("invalid", NA_real_), silent=TRUE), "try-error"))
    bq_value("fraction", 0.123456789123, "ratio")
    """
    out = subprocess.run(
        [r, "--vanilla", "-e", script, str(prelude)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=15,
    )
    if out.returncode == 77:
        pytest.skip("R jsonlite unavailable")
    assert out.returncode == 0, out.stderr
    from bioquorum.runtime.values import VALUE_MIME

    assert json.loads(out.stdout)["name"] == "fraction"
    assert parse_value(
        KernelOutput(stream=OutputStream.DISPLAY, data={VALUE_MIME: out.stdout})
    ) == NamedValue(name="fraction", value=0.123456789123, unit="ratio")


async def test_real_bilingual_file_binding_and_portable_replay(tmp_path):
    """Exercise invoke, both kernels, file ancestry and the exported standalone runner."""
    import json
    import subprocess
    from zipfile import ZipFile

    from bioquorum.application import verify, workspace
    from bioquorum.application.controls import ResearchControls
    from bioquorum.core.enums import Classification
    from bioquorum.review.binding import find_bindings
    from bioquorum.review.evidence import Evidence
    from bioquorum.tools import default_registry, invoke
    from bioquorum.tools.replay import dependency_chain

    project = Path(os.environ.get("BQ_R_PROJECT", "examples/v09")).resolve()
    python = project / ".pixi/envs/default/bin/python"
    if not python.is_file():
        pytest.skip("Provision examples/v09 with pixi or set BQ_R_PROJECT")
    root = tmp_path / "analysis"
    ws = workspace.init_project(root, classification=Classification.PUBLIC)
    ws.config["kernel"] = {"pixi": True, "macos_sandbox": sys.platform == "darwin"}
    # Use the separately provisioned locked prefix without copying the environment.
    (root / ".pixi").symlink_to(project / ".pixi", target_is_directory=True)
    for name in ("pixi.toml", "pixi.lock"):
        (root / name).write_bytes((project / name).read_bytes())
    (root / "input.csv").write_text("x\n3\n5\n")
    c = ResearchControls(ws)
    sid = c.new_session("Bilingual replay")
    ctx = workspace.tool_context(
        ws, c.store, sid, workspace.default_permissions(ws, allow=[], servers=[])
    )
    registry = default_registry()
    try:
        r = await invoke(
            registry.get("python_execute"),
            {
                "code": "import pandas as pd\ndf=pd.read_csv('input.csv')\ndf.to_csv('counts.csv',index=False)\nbq.value('n_py',len(df))",
                "input_files": ["input.csv"],
                "output_files": ["counts.csv"],
            },
            ctx,
        )
        assert not r.is_error, r.text()
        r = await invoke(
            registry.get("r_execute"),
            {
                "code": 'd <- read.csv("counts.csv"); bq_value("sum_r", sum(d$x))',
                "input_files": ["counts.csv"],
            },
            ctx,
        )
        assert not r.is_error, r.text()
    finally:
        await ctx.require_kernels().shutdown_all()
    history = c.store.history(sid)
    from bioquorum.core import events as ev

    starts = [p for event in history if isinstance(p := event.parsed(), ev.RunStarted)]
    assert [p.language for p in dependency_chain(history, starts[-1])] == ["python", "r"]
    evidence = Evidence.from_events(history)
    assert all(b.ok for b in find_bindings("{{n_py}} {{sum_r}}", evidence))
    assert evidence.values["sum_r"].value.value == 8
    out = await verify.export_bundle(c, sid)
    extracted = tmp_path / "standalone"
    with ZipFile(verify.bundle_file(c, sid, out["id"])) as archive:
        archive.extractall(extracted)
    result = subprocess.run(
        [str(python), str(extracted / "run.py")],
        cwd=extracted,
        env={"PATH": str(python.parent) + ":/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert len(rows) == 2 and all(
        row["outputs_matched"] and row["environment_matched"] for row in rows
    )
