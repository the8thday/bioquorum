"""Fixtures shared by tool, MCP and agent tests: a ToolContext over a FakeKernel."""

from __future__ import annotations

import pytest

from bioquorum.policy import PermissionPolicy
from bioquorum.provenance import ContentStore
from bioquorum.runtime import FakeKernel, KernelManager
from bioquorum.tools import ToolContext


@pytest.fixture
def kernel() -> FakeKernel:
    return FakeKernel(kernel_id="K")


@pytest.fixture
def ctx(store, tmp_path, kernel) -> ToolContext:
    async def factory(language):
        return kernel

    return ToolContext(
        store=store,
        session_id="S",
        project_id="P",
        permissions=PermissionPolicy.allow_all(),
        cas=ContentStore(tmp_path / "cas"),
        kernels=KernelManager(factory),
        workdir=tmp_path,
    )
