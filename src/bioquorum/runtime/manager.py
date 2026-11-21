"""One kernel per (session, language); runs within a session are serial (ARCHITECTURE.md §6.2)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from bioquorum.runtime import sandbox as sb
from bioquorum.runtime.kernel import Kernel

KernelFactory = Callable[[str], Awaitable[Kernel]]
"""``language -> started kernel``."""


def jupyter_factory(
    cwd: str | Path | None = None,
    *,
    sandbox: sb.SandboxSpec | None = None,
    require_isolation: bool = False,
    python_executable: Path | None = None,
    observe_lineage: bool = False,
) -> KernelFactory:
    """Factory for real Jupyter kernels started in ``cwd`` (the project root).

    With ``sandbox`` the kernel is isolated when this machine can do it (ADR-017). When it
    cannot: ``require_isolation`` raises :class:`~bioquorum.runtime.sandbox.IsolationUnavailable`
    before anything starts; otherwise the kernel runs unisolated and reports ``isolation="none"``.
    """

    async def make(language: str) -> Kernel:
        mode = sb.choose_isolation(
            sandbox,
            available=sb.bwrap_available(),
            required=require_isolation,
            macos_supported=bool(sandbox and sandbox.macos and sb.seatbelt_available()),
        )
        from bioquorum.runtime.jupyter import JupyterKernel  # lazy: jupyter_client is heavy

        if language == "r" and python_executable is None:
            raise ValueError(
                "R requires a provisioned project pixi environment with r-base and r-irkernel"
            )
        kernel_name = {"python": "python3", "r": "ir"}.get(language, language)
        kernel = JupyterKernel(
            kernel_name=kernel_name,
            language=language,
            cwd=cwd,
            python_executable=python_executable,
            observe_lineage=observe_lineage,
            sandbox=sandbox if mode is not sb.Isolation.NONE else None,
            isolation=mode,
        )
        try:
            return await kernel.start()
        except BaseException:
            await kernel.shutdown()
            raise

    return make


class KernelManager:
    def __init__(self, factory: KernelFactory | None = None) -> None:
        self._factory = factory or jupyter_factory()
        self._kernels: dict[tuple[str, str], Kernel] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._starting = asyncio.Lock()

    async def get(self, session_id: str, language: str = "python") -> Kernel:
        key = (session_id, language)
        async with self._starting:
            if key not in self._kernels:
                self._kernels[key] = await self._factory(language)
            return self._kernels[key]

    def lock(self, session_id: str) -> asyncio.Lock:
        """Hold this while executing to keep runs in one session serial."""
        return self._locks.setdefault(session_id, asyncio.Lock())

    def active(self) -> list[tuple[str, str]]:
        return list(self._kernels)

    async def shutdown(self, session_id: str) -> None:
        for key in [k for k in self._kernels if k[0] == session_id]:
            await self._kernels.pop(key).shutdown()

    async def shutdown_all(self) -> None:
        for kernel in self._kernels.values():
            await kernel.shutdown()
        self._kernels.clear()


class SessionKernels:
    """Own the per-session managers between workbench turns in this process."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[object, KernelManager]] = {}

    async def acquire(
        self, sid: str, configuration: object, candidate: KernelManager
    ) -> KernelManager:
        prior = self._sessions.get(sid)
        if prior and prior[0] == configuration:
            return prior[1]
        await self.release(sid)
        self._sessions[sid] = (configuration, candidate)
        return candidate

    async def release(self, sid: str) -> None:
        if prior := self._sessions.pop(sid, None):
            await prior[1].shutdown_all()

    async def close(self) -> None:
        for sid in list(self._sessions):
            await self.release(sid)
