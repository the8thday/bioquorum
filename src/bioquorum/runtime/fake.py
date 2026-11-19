"""Scripted kernel for tests (ARCHITECTURE.md §6.8). No processes, no Jupyter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from bioquorum.core.enums import OutputStream
from bioquorum.core.ids import new_id
from bioquorum.core.refs import DataRef
from bioquorum.runtime.kernel import (
    EndStatus,
    ExecutionEnd,
    KernelError,
    KernelEvent,
    KernelOutput,
)


@dataclass
class Script:
    """What the fake kernel does for one ``execute`` call.

    ``hang=True`` blocks until the timeout fires or ``interrupt()`` is called, which is how
    tests exercise ``run_timed_out`` and ``run_cancelled``.
    """

    outputs: list[KernelOutput] = field(default_factory=list)
    error: KernelError | None = None
    hang: bool = False

    @classmethod
    def stdout(cls, text: str) -> Script:
        return cls(outputs=[KernelOutput(stream=OutputStream.STDOUT, text=text)])

    @classmethod
    def fail(cls, ename: str, evalue: str) -> Script:
        return cls(error=KernelError(ename=ename, evalue=evalue))


Matcher = Callable[[str], bool]


class FakeKernel:
    """A :class:`~bioquorum.runtime.kernel.Kernel` that replays scripts.

    Scripts are matched in order: first by registered matchers (``on``), then the FIFO
    queue (``queue``). Unmatched code produces an empty successful run.
    """

    def __init__(self, *, language: str = "python", kernel_id: str | None = None) -> None:
        self._id = kernel_id or new_id()
        self._language = language
        self._rules: list[tuple[Matcher, Script]] = []
        self._queue: list[Script] = []
        self._interrupted = asyncio.Event()
        self.variables: dict[str, DataRef] = {}
        self.executed: list[str] = []
        self.restarts = 0
        self.closed = False

    @property
    def kernel_id(self) -> str:
        return self._id

    @property
    def language(self) -> str:
        return self._language

    synthetic = True

    @property
    def isolation(self) -> str:
        return "none"  # nothing runs, so there is nothing to isolate

    # ------------------------------------------------------------- scripting

    def on(self, match: str | Matcher, script: Script) -> FakeKernel:
        matcher: Matcher = (lambda c, s=match: s in c) if isinstance(match, str) else match
        self._rules.append((matcher, script))
        return self

    def queue(self, *scripts: Script) -> FakeKernel:
        self._queue.extend(scripts)
        return self

    def _script_for(self, code: str) -> Script:
        for matcher, script in self._rules:
            if matcher(code):
                return script
        if self._queue:
            return self._queue.pop(0)
        return Script()

    # ------------------------------------------------------------- Kernel

    async def execute(
        self, code: str, *, timeout: float | None = None
    ) -> AsyncIterator[KernelEvent]:
        if self.closed:
            raise RuntimeError("kernel is shut down")
        self.executed.append(code)
        self._interrupted.clear()
        script = self._script_for(code)
        for out in script.outputs:
            yield out
        if script.hang:
            try:
                await asyncio.wait_for(self._interrupted.wait(), timeout)
            except TimeoutError:
                yield ExecutionEnd(status=EndStatus.TIMED_OUT, timeout_s=timeout)
                return
            yield ExecutionEnd(status=EndStatus.CANCELLED)
            return
        if script.error is not None:
            yield ExecutionEnd(status=EndStatus.ERROR, error=script.error)
            return
        yield ExecutionEnd(status=EndStatus.OK)

    async def interrupt(self) -> None:
        self._interrupted.set()

    async def restart(self) -> None:
        self.restarts += 1
        self.variables.clear()

    async def snapshot_variables(self) -> dict[str, DataRef]:
        return dict(self.variables)

    async def shutdown(self) -> None:
        self.closed = True
