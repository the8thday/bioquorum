"""Kernel interface (ARCHITECTURE.md §6.2, ADR-009).

Everything above ``runtime/`` talks to kernels only through :class:`Kernel`. The
production implementation is :class:`bioquorum.runtime.jupyter.JupyterKernel`; tests use
:class:`bioquorum.runtime.fake.FakeKernel`.

``execute`` yields zero or more :class:`KernelOutput` items followed by exactly one
:class:`ExecutionEnd`. Implementations must always end the stream, including on timeout
and interrupt, so no Run is ever left hanging.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from bioquorum.core.enums import OutputStream
from bioquorum.core.refs import DataRef


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class KernelOutput(_Frozen):
    """One piece of output. ``data`` maps MIME type to payload (binary payloads base64)."""

    stream: OutputStream
    text: str | None = None
    data: dict[str, str] = Field(default_factory=dict)


class EndStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class KernelError(_Frozen):
    ename: str
    evalue: str
    traceback: list[str] = Field(default_factory=list)

    def short(self) -> str:
        return f"{self.ename}: {self.evalue}"


class ExecutionEnd(_Frozen):
    status: EndStatus
    error: KernelError | None = None
    timeout_s: float | None = None


KernelEvent = KernelOutput | ExecutionEnd


@runtime_checkable
class Kernel(Protocol):
    @property
    def kernel_id(self) -> str: ...

    @property
    def language(self) -> str: ...

    def execute(self, code: str, *, timeout: float | None = None) -> AsyncIterator[KernelEvent]:
        """Run ``code``. Yields outputs, then exactly one ExecutionEnd."""
        ...

    async def interrupt(self) -> None: ...

    async def restart(self) -> None: ...

    async def snapshot_variables(self) -> dict[str, DataRef]: ...

    async def shutdown(self) -> None: ...
