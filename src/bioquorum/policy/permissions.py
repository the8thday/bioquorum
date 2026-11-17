"""Permission decisions (ARCHITECTURE.md §6.7).

The policy answers "may this happen?" for a (permission class, target) pair. It remembers
grants by scope and asks a pluggable ``Asker`` (CLI prompt, MCP elicitation, test stub)
for anything it has not seen. Without an asker every unknown request is denied: secure by
default (principle 10). Writing the corresponding events is the caller's job
(``tools.invoke``), so this module stays storage-free.

v0.1 keeps grants in memory; persisting project/global grants comes with the config work.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePath

from bioquorum.core.enums import PermissionClass, PermissionScope


@dataclass(frozen=True)
class PermissionRequest:
    permission: PermissionClass
    target: str
    session_id: str | None = None
    project_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class Decision:
    granted: bool
    scope: PermissionScope | None = None
    asked: bool = False
    """True when the asker was consulted (so the caller must record the decision)."""


@dataclass(frozen=True)
class Grant:
    permission: PermissionClass
    target: str  # "*" or exact target or directory prefix
    scope: PermissionScope
    session_id: str | None = None
    project_id: str | None = None


Asker = Callable[[PermissionRequest], Awaitable[Decision]]


def covers(grant_target: str, target: str) -> bool:
    """``*`` covers everything; a path covers itself and everything below it."""
    if grant_target in ("*", target):
        return True
    root = PurePath(os.path.normpath(grant_target))
    # normpath collapses "..", so "/data/../etc" is not under "/data". Tools pass resolved
    # paths (symlinks followed) as targets; this is the second line of defence.
    return root.is_absolute() and PurePath(os.path.normpath(target)).is_relative_to(root)


class PermissionPolicy:
    def __init__(self, ask: Asker | None = None) -> None:
        self._ask = ask
        self._grants: list[Grant] = []

    @classmethod
    def allow_all(cls) -> PermissionPolicy:
        """For tests and explicit ``--yes`` runs. Never the default."""
        policy = cls()
        for perm in PermissionClass:
            policy.grant(perm, "*", PermissionScope.GLOBAL)
        return policy

    def grant(
        self,
        permission: PermissionClass,
        target: str,
        scope: PermissionScope,
        *,
        session_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        if scope is PermissionScope.ONCE:
            return  # one-shot grants are not remembered
        self._grants.append(Grant(permission, target, scope, session_id, project_id))

    def revoke(self, permission: PermissionClass, target: str) -> int:
        before = len(self._grants)
        self._grants = [
            g for g in self._grants if not (g.permission is permission and g.target == target)
        ]
        return before - len(self._grants)

    def grants(self) -> list[Grant]:
        return list(self._grants)

    def lookup(self, req: PermissionRequest) -> Decision | None:
        for g in self._grants:
            if g.permission is not req.permission or not covers(g.target, req.target):
                continue
            if g.scope is PermissionScope.SESSION and g.session_id != req.session_id:
                continue
            if g.scope is PermissionScope.PROJECT and g.project_id != req.project_id:
                continue
            return Decision(granted=True, scope=g.scope)
        return None

    async def decide(self, req: PermissionRequest) -> Decision:
        known = self.lookup(req)
        if known is not None:
            return known
        if self._ask is None:
            return Decision(granted=False, asked=False)
        decision = await self._ask(req)
        decision = Decision(granted=decision.granted, scope=decision.scope, asked=True)
        if decision.granted and decision.scope is not None:
            self.grant(
                req.permission,
                req.target,
                decision.scope,
                session_id=req.session_id,
                project_id=req.project_id,
            )
        return decision
