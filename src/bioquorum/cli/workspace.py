"""Composition root: turn a project directory into a ready ToolContext and registry.

Layout of a project::

    <root>/bioquorum.toml          # optional; [mcp.servers.<name>] etc.
    <root>/.bioquorum/project.json # project id, name, classification
    <root>/.bioquorum/bioquorum.db # event store
    <root>/.bioquorum/cas/         # content-addressed store

Only the CLI (top layer) assembles these pieces, so every lower layer stays injectable.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, PermissionClass, PermissionScope
from bioquorum.core.ids import new_id
from bioquorum.mcp.client import ServerConfig
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.provenance.cas import ContentStore
from bioquorum.runtime.manager import KernelFactory, KernelManager, jupyter_factory
from bioquorum.storage.db import Database
from bioquorum.storage.events import EventStore
from bioquorum.tools.context import ToolContext

STATE_DIR = ".bioquorum"
CONFIG_FILE = "bioquorum.toml"


class WorkspaceError(Exception):
    pass


@dataclass
class Workspace:
    root: Path
    project_id: str
    name: str
    classification: Classification
    data_dirs: list[Path] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    def database(self) -> Database:
        return Database.at(self.state_dir / "bioquorum.db")

    def cas(self) -> ContentStore:
        return ContentStore(self.state_dir / "cas")

    def mcp_servers(self) -> list[ServerConfig]:
        servers = self.config.get("mcp", {}).get("servers", {})
        out = []
        for name, data in servers.items():
            data = dict(data)
            if data.get("replay"):  # cassette paths are relative to the project root
                data["replay"] = str((self.root / data["replay"]).resolve())
            out.append(ServerConfig.from_dict(name, data))
        return out

    def readable_roots(self) -> list[Path]:
        return [self.root, *self.data_dirs]


def find_root(start: Path) -> Path | None:
    for d in (start.resolve(), *start.resolve().parents):
        if (d / STATE_DIR / "project.json").is_file():
            return d
    return None


def init_project(
    root: Path, *, name: str | None = None, classification: Classification = Classification.INTERNAL
) -> Workspace:
    root = root.resolve()
    meta = root / STATE_DIR / "project.json"
    if meta.exists():
        raise WorkspaceError(f"already a BioQuorum project: {root}")
    ws = Workspace(
        root=root, project_id=new_id(), name=name or root.name, classification=classification
    )
    ws.state_dir.mkdir(parents=True, exist_ok=True)
    EventStore(ws.database()).append(
        ev.ProjectCreated(
            project_id=ws.project_id,
            name=ws.name,
            root_dir=str(root),
            classification=classification,
        ),
        session_id=None,
        actor=Actor.USER,
    )
    meta.write_text(
        json.dumps(
            {"project_id": ws.project_id, "name": ws.name, "classification": classification.value},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return ws


def load(start: Path) -> Workspace:
    root = find_root(start)
    if root is None:
        raise WorkspaceError(f"no BioQuorum project at or above {start}; run `bq project init`")
    meta = json.loads((root / STATE_DIR / "project.json").read_text(encoding="utf-8"))
    config: dict[str, Any] = {}
    cfg = root / CONFIG_FILE
    if cfg.is_file():
        config = tomllib.loads(cfg.read_text(encoding="utf-8"))
    project = config.get("project", {})
    data_dirs = [(root / d).resolve() for d in project.get("data_dirs", [])]
    return Workspace(
        root=root,
        project_id=meta["project_id"],
        name=meta["name"],
        classification=Classification(meta["classification"]),
        data_dirs=data_dirs,
        config=config,
    )


def start_session(ws: Workspace, store: EventStore, title: str) -> str:
    session_id = new_id()
    store.append(
        ev.SessionCreated(project_id=ws.project_id, title=title),
        session_id=session_id,
        actor=Actor.USER,
    )
    return session_id


def default_permissions(
    ws: Workspace, *, allow: list[PermissionClass], servers: list[ServerConfig]
) -> PermissionPolicy:
    """What starting a BioQuorum server implies the user has agreed to.

    Reading inside the project and its data dirs, and running Python, are granted for the
    project. Configured MCP servers count as consented to. Everything else (network,
    connectors) needs an explicit ``--allow``. The MCP host usually adds its own per-call
    confirmation on top.
    """
    policy = PermissionPolicy()
    scope, pid = PermissionScope.PROJECT, ws.project_id
    for d in ws.readable_roots():
        policy.grant(PermissionClass.READ_FS, str(d), scope, project_id=pid)
    policy.grant(PermissionClass.EXECUTE, "python", scope, project_id=pid)
    for s in servers:
        policy.grant(PermissionClass.EXTERNAL_MCP, f"mcp:{s.name}", scope, project_id=pid)
    for perm in allow:
        policy.grant(perm, "*", PermissionScope.GLOBAL)
    return policy


def tool_context(
    ws: Workspace,
    store: EventStore,
    session_id: str,
    permissions: PermissionPolicy,
    *,
    kernel_factory: KernelFactory | None = None,
) -> ToolContext:
    return ToolContext(
        store=store,
        session_id=session_id,
        permissions=permissions,
        project_id=ws.project_id,
        classification=ws.classification,
        cas=ws.cas(),
        kernels=KernelManager(kernel_factory or jupyter_factory(ws.root)),
        workdir=ws.root,
    )
