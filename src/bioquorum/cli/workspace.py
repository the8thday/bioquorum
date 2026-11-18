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
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, PermissionClass, PermissionScope
from bioquorum.core.ids import new_id
from bioquorum.mcp.client import ServerConfig
from bioquorum.models.config import ModelsConfig
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.provenance.cas import ContentStore
from bioquorum.runtime.manager import KernelFactory, KernelManager, jupyter_factory
from bioquorum.runtime.sandbox import Isolation, SandboxSpec, bwrap_available, choose_isolation
from bioquorum.storage.db import Database
from bioquorum.storage.events import EventStore
from bioquorum.tools.context import ToolContext

STATE_DIR = ".bioquorum"
CONFIG_FILE = "bioquorum.toml"


def global_config_path() -> Path:
    """``$BQ_CONFIG`` if set (tests point it elsewhere), else ``~/.bioquorum/config.toml``."""
    env = os.environ.get("BQ_CONFIG")
    return Path(env) if env else Path.home() / ".bioquorum" / "config.toml"


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


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

    def models_config(self) -> ModelsConfig:
        return ModelsConfig.from_dict(self.config)

    def readable_roots(self) -> list[Path]:
        return [self.root, *self.data_dirs]

    # ---------------------------------------------------------- kernel isolation (ADR-017)

    @property
    def isolation_required(self) -> bool:
        """Only public projects may ever run code outside the sandbox."""
        return self.classification is not Classification.PUBLIC

    def kernel_network(self) -> bool:
        """``[kernel] network = true``: the user's explicit choice, public projects only."""
        wanted = bool(self.config.get("kernel", {}).get("network", False))
        if wanted and self.isolation_required:
            raise WorkspaceError(
                f"[kernel] network = true is only allowed in public projects; this project is "
                f"{self.classification.value}. Kernel code reaches the network through tools."
            )
        return wanted

    def kernel_sandbox(self) -> SandboxSpec:
        """Project dir read-write; data dirs read-only. The kernel must not touch BioQuorum's
        own state (event log, CAS) or the files that decide what runs outside the sandbox
        next time (MCP server commands in the config, git hooks)."""
        protected = [self.root / CONFIG_FILE, self.root / ".git"]
        return SandboxSpec(
            workdir=self.root,
            read_only=(*self.data_dirs, *protected),
            hidden=(self.state_dir,),
            network=self.kernel_network(),
        )

    def kernel_isolation(self) -> Isolation:
        """What kernels of this project will get here. Raises ``IsolationUnavailable`` when the
        project requires isolation and this machine cannot provide it."""
        return choose_isolation(
            self.kernel_sandbox(), available=bwrap_available(), required=self.isolation_required
        )


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


def load(start: Path, *, global_config: Path | None = None) -> Workspace:
    root = find_root(start)
    if root is None:
        raise WorkspaceError(f"no BioQuorum project at or above {start}; run `bq project init`")
    meta = json.loads((root / STATE_DIR / "project.json").read_text(encoding="utf-8"))
    config: dict[str, Any] = {}
    global_config = global_config or global_config_path()
    if global_config.is_file():
        config = tomllib.loads(global_config.read_text(encoding="utf-8"))
    cfg = root / CONFIG_FILE
    if cfg.is_file():  # project settings override global ones
        config = _merge(config, tomllib.loads(cfg.read_text(encoding="utf-8")))
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
    if kernel_factory is None:
        # A read-only mount needs an existing file; without one, kernel code could create
        # the config itself and plant an MCP server command for the next start.
        (ws.root / CONFIG_FILE).touch(exist_ok=True)
        kernel_factory = jupyter_factory(
            ws.root, sandbox=ws.kernel_sandbox(), require_isolation=ws.isolation_required
        )
    return ToolContext(
        store=store,
        session_id=session_id,
        permissions=permissions,
        project_id=ws.project_id,
        classification=ws.classification,
        cas=ws.cas(),
        kernels=KernelManager(kernel_factory),
        workdir=ws.root,
    )
