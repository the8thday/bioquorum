"""One kernel per (session, language); runs within a session are serial (ARCHITECTURE.md §6.2)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from bioquorum.runtime.kernel import Kernel

KernelFactory = Callable[[str], Awaitable[Kernel]]
"""``language -> started kernel``."""


def jupyter_factory(cwd: str | Path | None = None) -> KernelFactory:
    """Factory for real Jupyter kernels started in ``cwd`` (the project root)."""

    async def make(language: str) -> Kernel:
        from bioquorum.runtime.jupyter import JupyterKernel  # lazy: jupyter_client is heavy

        kernel_name = {"python": "python3", "r": "ir"}.get(language, language)
        return await JupyterKernel(kernel_name=kernel_name, language=language, cwd=cwd).start()

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
