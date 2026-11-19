"""Production kernel on the Jupyter protocol (ADR-009). The only module importing jupyter_client.

Tests for this module are marked ``integration`` because they start a real ipykernel.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jupyter_client.manager import AsyncKernelManager

from bioquorum.core.enums import DataKind, OutputStream
from bioquorum.core.hashing import DATAFRAME_PARQUET_KWARGS, sha256_text
from bioquorum.core.ids import new_id
from bioquorum.core.refs import DataRef
from bioquorum.runtime.kernel import (
    EndStatus,
    ExecutionEnd,
    KernelError,
    KernelEvent,
    KernelOutput,
)
from bioquorum.runtime.lineage import PRELUDE as LINEAGE_PRELUDE
from bioquorum.runtime.sandbox import Isolation, SandboxSpec, bwrap_argv, scrub_env, seatbelt_argv
from bioquorum.runtime.values import PRELUDE

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


_ENVIRONMENT = """
def __bq_environment():
    import json, platform, sys
    from importlib.metadata import distributions
    packages = sorted((dist.metadata["Name"] or "", dist.version) for dist in distributions())
    print(json.dumps([sys.version, platform.system(), platform.machine(), packages]))
__bq_environment()
del __bq_environment
"""
_MAX_SOCKET_PATH = 90  # AF_UNIX paths are limited to ~104-108 bytes; leave room for "-k-N"


def _socket_dir() -> str:
    d = tempfile.mkdtemp(prefix="bq-")  # mode 0700
    if len(d) > _MAX_SOCKET_PATH:  # long $TMPDIR (e.g. macOS): fall back to /tmp
        os.rmdir(d)
        d = tempfile.mkdtemp(prefix="bq-", dir="/tmp")
    return d


def _mime_payload(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


class _SandboxedKernelManager(AsyncKernelManager):
    """Starts the kernel command inside bubblewrap (ADR-017)."""

    bq_sandbox: SandboxSpec | None = None
    bq_python: str | None = None
    bq_writable: tuple[str, ...] = ()
    bq_isolation: Isolation = Isolation.BWRAP

    def format_kernel_cmd(self, extra_arguments: list[str] | None = None) -> list[str]:
        cmd = super().format_kernel_cmd(extra_arguments)
        if self.bq_python:
            cmd[0] = self.bq_python
        if self.bq_sandbox is None:
            return cmd
        if self.bq_isolation is Isolation.SEATBELT:
            return (
                seatbelt_argv(self.bq_sandbox, executable=cmd[0], writable=self.bq_writable) + cmd
            )
        prefix = bwrap_argv(self.bq_sandbox, executable=cmd[0], writable=self.bq_writable)
        return prefix + cmd


class JupyterKernel:
    def __init__(
        self,
        *,
        kernel_name: str = "python3",
        language: str = "python",
        cwd: str | Path | None = None,
        sandbox: SandboxSpec | None = None,
        isolation: Isolation | None = None,
        python_executable: Path | None = None,
        observe_lineage: bool = False,
    ) -> None:
        """``sandbox`` set: the kernel runs inside bubblewrap. The caller has already checked
        that isolation is available (``runtime.sandbox.choose_isolation``)."""
        self._id = new_id()
        self._language = language
        self._cwd = str(cwd) if cwd is not None else None
        self._sandbox = sandbox
        self._isolation = (
            isolation
            if isolation is not None
            else (
                Isolation.NONE
                if sandbox is None
                else Isolation.BWRAP_NET
                if sandbox.network
                else Isolation.BWRAP
            )
        )
        self._observe_lineage = observe_lineage
        if sandbox is not None and sys.platform == "win32":
            raise RuntimeError("kernel sandboxing is not supported on Windows")
        # IPC (Unix sockets, user-only permissions) instead of unencrypted localhost TCP,
        # which any local user could connect to and run code through.
        self._sock_dir: str | None = None
        if sys.platform == "win32":
            self._km = _SandboxedKernelManager(kernel_name=kernel_name)
        else:
            # Absolute socket path in a 0700 dir: the kernel runs in `cwd`, the client does
            # not, so a relative path would point at two different places.
            self._sock_dir = _socket_dir()
            self._km = _SandboxedKernelManager(
                kernel_name=kernel_name, transport="ipc", ip=os.path.join(self._sock_dir, "k")
            )
            if sandbox is not None:
                # The connection file must be readable inside the sandbox: keep it next to
                # the sockets instead of in the user's Jupyter runtime dir.
                self._km.connection_file = os.path.join(self._sock_dir, "connection.json")
                self._km.bq_sandbox = sandbox
                self._km.bq_writable = (self._sock_dir,)
        self._km.bq_python = str(python_executable) if python_executable else None
        self._km.bq_isolation = self._isolation
        self.environment: str | None = None
        self._kc: Any = None
        self._cancel_requested = False

    @property
    def kernel_id(self) -> str:
        return self._id

    @property
    def language(self) -> str:
        return self._language

    @property
    def isolation(self) -> str:
        return self._isolation

    async def start(self, *, ready_timeout: float = 60.0) -> JupyterKernel:
        # Secrets stay out of kernel code in every mode: the sandbox clears the environment,
        # an unsandboxed kernel gets it with secret-looking names removed.
        kw: dict[str, Any] = {"env": scrub_env(os.environ)}
        if self._isolation is Isolation.SEATBELT:
            assert self._sock_dir is not None
            kw["env"] = {
                "PATH": "/usr/bin:/bin",
                "HOME": self._sock_dir,
                "TMPDIR": self._sock_dir,
                "LANG": "en_US.UTF-8",
                "JUPYTER_CONFIG_DIR": self._sock_dir,
            }
        if self._cwd:
            kw["cwd"] = self._cwd
        await self._km.start_kernel(**kw)
        if self._sandbox is not None:
            # A signal would hit bwrap (and take the kernel down with it); ask the kernel
            # to interrupt itself over the control channel instead.
            spec = self._km.kernel_spec
            assert spec is not None  # loaded by start_kernel
            spec.interrupt_mode = "message"
        self._kc = self._km.client()
        self._kc.start_channels()
        await self._kc.wait_for_ready(timeout=ready_timeout)
        await self._install_prelude()
        return self

    async def _install_prelude(self) -> None:
        """Define ``bq`` in the kernel (ADR-014). Our own code: a failure here is a bug."""
        async for item in self.execute(PRELUDE, timeout=30, silent=True):
            if isinstance(item, ExecutionEnd) and item.status is not EndStatus.OK:
                detail = item.error.short() if item.error else item.status.value
                raise RuntimeError(f"kernel prelude failed: {detail}")

        if self.language == "python":
            chunks = []
            async for part in self.execute(_ENVIRONMENT, timeout=20, silent=True):
                if isinstance(part, KernelOutput) and part.stream is OutputStream.STDOUT:
                    chunks.append(part.text or "")
                elif isinstance(part, ExecutionEnd) and part.status is not EndStatus.OK:
                    chunks.clear()
            fingerprint = "".join(chunks).strip()
            self.environment = sha256_text(fingerprint) if fingerprint else None

        if self.language == "python" and self._observe_lineage:
            async for _ in self.execute(LINEAGE_PRELUDE, timeout=45, silent=True):
                pass

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
        await self._install_prelude()

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
        if self._sock_dir is not None:
            shutil.rmtree(self._sock_dir, ignore_errors=True)
