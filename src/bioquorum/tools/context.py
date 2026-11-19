"""Everything a tool call needs from its surroundings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bioquorum.core.enums import Actor, Classification
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.provenance.cas import ContentStore
from bioquorum.runtime.manager import KernelManager
from bioquorum.storage.events import EventStore


@dataclass
class ToolContext:
    store: EventStore
    session_id: str
    permissions: PermissionPolicy
    project_id: str | None = None
    classification: Classification = Classification.INTERNAL
    cas: ContentStore | None = None
    kernels: KernelManager | None = None
    env_lock_hash: str | None = None
    outbound_proxy: str | None = None
    verification_run: bool = False
    workdir: Path = field(default_factory=Path.cwd)
    actor: Actor = Actor.AGENT
    # Set by invoke() for the duration of one call:
    call_id: str = ""
    requested_event_id: str | None = None

    def require_cas(self) -> ContentStore:
        if self.cas is None:
            raise RuntimeError("this tool needs a content store (ToolContext.cas)")
        return self.cas

    def require_kernels(self) -> KernelManager:
        if self.kernels is None:
            raise RuntimeError("this tool needs a kernel manager (ToolContext.kernels)")
        return self.kernels
