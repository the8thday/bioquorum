"""Production kernel on the Jupyter protocol (ADR-009). The only module importing jupyter_client.

Tests for this module are marked ``integration`` because they start a real ipykernel.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from jupyter_client.manager import AsyncKernelManager

from bioquorum.core.enums import DataKind, OutputStream
from bioquorum.core.hashing import DATAFRAME_PARQUET_KWARGS
from bioquorum.core.ids import new_id
from bioquorum.core.refs import DataRef
from bioquorum.runtime.kernel import (
    EndStatus,
    ExecutionEnd,
    KernelError,
    KernelEvent,
    KernelOutput,
)

_DRAIN_S = 5.0  # how long to wait for the kernel to go idle after an interrupt

# Runs inside the kernel. Hashes str/bytes and DataFrames with the canonical rules from
# core.hashing; everything else is skipped (weak hashes are not useful for snapshots).
_SNAPSHOT = r"""
def __bq_snapshot():
    import hashlib, io, json, sys
    out = {}
    pd = sys.modules.get("pandas")
    for name, value in list(globals().items()):
        if name.startswith("_"):
            continue
        try:
            if isinstance(value, (bytes, str)):
                raw = value.encode("utf-8") if isinstance(value, str) else value
                out[name] = ["other", hashlib.sha256(raw).hexdigest(), len(raw)]
            elif pd is not None and isinstance(value, pd.DataFrame):
                buf = io.BytesIO()
                value[sorted(value.columns, key=str)].to_parquet(buf, **__BQ_KW__)
                raw = buf.getvalue()
                out[name] = ["dataframe", hashlib.sha256(raw).hexdigest(), len(raw)]
        except Exception:
            continue
    print(json.dumps(out))
__bq_snapshot()
del __bq_snapshot
""".replace("__BQ_KW__", repr(DATAFRAME_PARQUET_KWARGS))


def _mime_payload(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


class JupyterKernel:
    def __init__(self, *, kernel_name: str = "python3", language: str = "python") -> None:
        self._id = new_id()
        self._language = language
        self._km = AsyncKernelManager(kernel_name=kernel_name)
        self._kc: Any = None
        self._cancel_requested = False

    @property
    def kernel_id(self) -> str:
        return self._id

    @property
    def language(self) -> str:
        return self._language

    async def start(self, *, ready_timeout: float = 60.0) -> JupyterKernel:
        await self._km.start_kernel()
        self._kc = self._km.client()
        self._kc.start_channels()
        await self._kc.wait_for_ready(timeout=ready_timeout)
        return self

    # ------------------------------------------------------------- Kernel

    async def execute(
        self, code: str, *, timeout: float | None = None, silent: bool = False
    ) -> AsyncIterator[KernelEvent]:
        if self._kc is None:
            raise RuntimeError("kernel not started; call await kernel.start()")
        self._cancel_requested = False
        # `silent` only skips history: ipykernel may suppress iopub output for truly silent requests.
        msg_id = self._kc.execute(code, store_history=not silent)
        deadline = None if timeout is None else time.monotonic() + timeout
        error: KernelError | None = None
        timed_out = False

        while True:
            if deadline is not None and not timed_out:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    await self._km.interrupt_kernel()
                    deadline = time.monotonic() + _DRAIN_S
                    continue
                wait = remaining
            elif deadline is not None:  # draining after timeout
                wait = max(deadline - time.monotonic(), 0.01)
            else:
                wait = 1.0
            try:
                msg = await self._kc.get_iopub_msg(timeout=wait)
            except asyncio.CancelledError:
                raise
            except Exception:  # queue.Empty: no message within `wait`
                if timed_out and deadline is not None and time.monotonic() >= deadline:
                    break  # kernel did not go idle; give up draining, still end the run
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            kind, content = msg["msg_type"], msg["content"]
            if kind == "stream":
                stream = OutputStream.STDERR if content["name"] == "stderr" else OutputStream.STDOUT
                yield KernelOutput(stream=stream, text=content["text"])
            elif kind in ("execute_result", "display_data"):
                data = {k: _mime_payload(v) for k, v in content.get("data", {}).items()}
                yield KernelOutput(stream=OutputStream.DISPLAY, data=data)
            elif kind == "error":
                error = KernelError(
                    ename=content["ename"],
                    evalue=content["evalue"],
                    traceback=list(content.get("traceback", [])),
                )
            elif kind == "status" and content.get("execution_state") == "idle":
                break

        if timed_out:
            yield ExecutionEnd(status=EndStatus.TIMED_OUT, timeout_s=timeout)
        elif self._cancel_requested:
            yield ExecutionEnd(status=EndStatus.CANCELLED)
        elif error is not None:
            yield ExecutionEnd(status=EndStatus.ERROR, error=error)
        else:
            yield ExecutionEnd(status=EndStatus.OK)

    async def interrupt(self) -> None:
        self._cancel_requested = True
        await self._km.interrupt_kernel()

    async def restart(self) -> None:
        await self._km.restart_kernel()
        await self._kc.wait_for_ready(timeout=60)

    async def snapshot_variables(self) -> dict[str, DataRef]:
        text = ""
        async for ev in self.execute(_SNAPSHOT, timeout=60, silent=True):
            if isinstance(ev, KernelOutput) and ev.stream is OutputStream.STDOUT and ev.text:
                text += ev.text
        try:
            raw: dict[str, list[Any]] = json.loads(text.strip().splitlines()[-1]) if text else {}
        except (json.JSONDecodeError, IndexError):
            return {}
        return {
            name: DataRef(
                uri=f"kernel://{self._id}/{name}",
                content_hash=h,
                size=size,
                kind=DataKind(kind),
            )
            for name, (kind, h, size) in raw.items()
        }

    async def shutdown(self) -> None:
        if self._kc is not None:
            self._kc.stop_channels()
        await self._km.shutdown_kernel(now=True)
