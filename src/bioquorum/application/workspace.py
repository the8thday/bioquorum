"""Composition root: turn a project directory into a ready ToolContext and registry.

Layout of a project::

    <root>/bioquorum.toml          # optional; [mcp.servers.<name>] etc.
    <root>/.bioquorum/project.json # project id, name, classification
    <root>/.bioquorum/bioquorum.db # event store
    <root>/.bioquorum/cas/         # content-addressed store

CLI and workbench share this composition so lower layers stay injectable.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from bioquorum.agent.budget import Budget
from bioquorum.application.environment import project_environment
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, PermissionClass, PermissionScope
from bioquorum.core.ids import new_id
from bioquorum.mcp.client import McpConnection, ServerConfig, StdioLaunch
from bioquorum.models.config import ModelsConfig
from bioquorum.policy.egress import loopback_proxy_url
from bioquorum.policy.permissions import Asker, PermissionPolicy
from bioquorum.policy.secrets import redact
from bioquorum.provenance.cas import ContentStore
from bioquorum.runtime.manager import KernelFactory, KernelManager, jupyter_factory
from bioquorum.runtime.sandbox import (
    Isolation,
    SandboxSpec,
    bwrap_argv,
    bwrap_available,
    choose_isolation,
    seatbelt_argv,
    seatbelt_available,
)
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


@dataclass(frozen=True)
class AgentOptions:
    """``[agent]`` switches (ADR-018): each mechanism can be turned off on its own."""

    binding: bool = True  # evidence-binding prompt and source labels (ADR-014)
    binding_retries: int = 0  # opt in to sending unresolved bindings back
    budget: Budget = field(default_factory=Budget)
    compact_at_bytes: int | None = None
    stream: bool = False
    show_cost: bool = True
    skills: bool = False
    project_notes: bool = False
    literature_subtasks: bool = False
    literature_tools: tuple[str, ...] = ()
    source_to_kernel: bool = False
    vision: bool = False


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

    def outbound_proxy(self) -> str | None:
        value = self.config.get("security", {}).get("outbound_proxy")
        try:
            return None if value is None else loopback_proxy_url(value)
        except ValueError as exc:
            raise WorkspaceError(f"[security] {exc}") from None

    def mcp_connection(self, cfg: ServerConfig) -> McpConnection:
        @contextmanager
        def sandboxed_command():
            assert cfg.command is not None
            executable = (
                str((self.root / cfg.command).absolute())
                if os.sep in cfg.command
                else shutil.which(cfg.command)
            )
            if executable is None or not os.access(executable, os.X_OK):
                raise WorkspaceError("The selected isolated MCP executable is unavailable")
            if Path(executable).absolute() != Path(sys.executable).absolute():
                executable = str(Path(executable).resolve(strict=True))
            roots = tuple((self.root / path).resolve(strict=True) for path in cfg.read_paths)
            with TemporaryDirectory(prefix="bq-mcp-") as workdir:
                spec = SandboxSpec(workdir=workdir, read_only=roots, macos=True, standalone=True)
                mode = choose_isolation(
                    spec,
                    available=bwrap_available(),
                    macos_supported=seatbelt_available(),
                    required=True,
                )
                prefix = (
                    seatbelt_argv(spec, executable=executable, writable=(workdir,))
                    if mode is Isolation.SEATBELT
                    else bwrap_argv(spec, executable=executable)
                )
                yield StdioLaunch(
                    tuple([*prefix, executable, *cfg.args]),
                    workdir,
                    {"PATH": "/usr/bin:/bin", "HOME": workdir, "TMPDIR": workdir},
                )

        return McpConnection(
            cfg,
            outbound_proxy=self.outbound_proxy(),
            isolated_stdio=sandboxed_command if cfg.isolated else None,
        )

    def models_config(self) -> ModelsConfig:
        return ModelsConfig.from_dict(self.config)

    def agent_options(self) -> AgentOptions:
        cfg = self.config.get("agent", {})
        for flag in (
            "skills",
            "project_notes",
            "literature_subtasks",
            "source_to_kernel",
            "vision",
        ):
            if type(cfg.get(flag, False)) is not bool:
                raise WorkspaceError(f"[agent] {flag} must be true or false")
        literature = cfg.get("literature_tools", [])
        if (
            not isinstance(literature, list)
            or len(literature) > 12
            or any(not isinstance(name, str) or not 1 <= len(name) <= 64 for name in literature)
        ):
            raise WorkspaceError("[agent] literature_tools must list up to twelve exact tool names")
        if cfg.get("literature_subtasks") and not literature:
            raise WorkspaceError(
                "[agent] literature_subtasks requires an explicit literature_tools allowlist"
            )
        retries = cfg.get("binding_retries", AgentOptions.binding_retries)
        if type(retries) is not int or not 0 <= retries <= 2:
            raise WorkspaceError("[agent] binding_retries must be 0, 1 or 2")
        threshold = cfg.get("compact_at_bytes")
        if threshold is not None and (type(threshold) is not int or threshold <= 0):
            raise WorkspaceError("[agent] compact_at_bytes must be a positive integer")
        try:
            budget = Budget(
                tokens=cfg.get("budget_tokens"),
                usd=cfg.get("budget_usd"),
                seconds=cfg.get("budget_seconds"),
            )
        except ValueError as exc:
            raise WorkspaceError(f"[agent] {exc}") from exc
        return AgentOptions(
            binding=bool(cfg.get("binding", True)),
            binding_retries=retries,
            budget=budget,
            compact_at_bytes=threshold,
            stream=bool(cfg.get("stream", False)),
            show_cost=bool(cfg.get("show_cost", True)),
            skills=cfg.get("skills", False),
            project_notes=cfg.get("project_notes", False),
            literature_subtasks=cfg.get("literature_subtasks", False),
            literature_tools=tuple(literature),
            source_to_kernel=cfg.get("source_to_kernel", False),
            vision=cfg.get("vision", False),
        )

    def readable_roots(self) -> list[Path]:
        return [self.root, *self.data_dirs]

    # ---------------------------------------------------------- kernel isolation (ADR-017)

    @property
    def isolation_required(self) -> bool:
        """Only public projects may ever run code outside the sandbox."""
        return self.classification is not Classification.PUBLIC

    def kernel_network(self) -> bool:
        """``[kernel] network = true``: the user's explicit choice, public projects only."""
        wanted = self.config.get("kernel", {}).get("network", False)
        if type(wanted) is not bool:
            raise WorkspaceError("[kernel] network must be true or false")
        if wanted and self.isolation_required:
            raise WorkspaceError(
                f"[kernel] network = true is only allowed in public projects; this project is "
                f"{self.classification.value}. Kernel code reaches the network through tools."
            )
        if wanted and self.outbound_proxy() is not None:
            raise WorkspaceError(
                "Kernel network is unavailable when an outbound proxy is configured"
            )
        return wanted

    def kernel_sandbox(self) -> SandboxSpec:
        """Project dir read-write; data dirs read-only. The kernel must not touch BioQuorum's
        own state (event log, CAS) or the files that decide what runs outside the sandbox
        next time (MCP server commands in the config, git hooks)."""
        protected = [self.root / CONFIG_FILE, self.root / ".git"]
        macos = self.config.get("kernel", {}).get("macos_sandbox", False)
        if type(macos) is not bool:
            raise WorkspaceError("[kernel] macos_sandbox must be true or false")
        return SandboxSpec(
            workdir=self.root,
            read_only=(*self.data_dirs, *protected),
            hidden=(self.state_dir,),
            network=self.kernel_network(),
            macos=macos,
        )

    def requires_sandbox(self, spec: SandboxSpec) -> bool:
        return self.isolation_required or spec.macos or self.outbound_proxy() is not None

    def kernel_isolation(self) -> Isolation:
        """What kernels of this project will get here. Raises ``IsolationUnavailable`` when the
        project requires isolation and this machine cannot provide it."""
        spec = self.kernel_sandbox()
        return choose_isolation(
            spec,
            available=bwrap_available(),
            macos_supported=spec.macos and seatbelt_available(),
            required=self.requires_sandbox(spec),
        )


@contextmanager
def project_lock(root: Path):
    """Own project operations and crash recovery across CLI and workbench processes."""
    with (root / STATE_DIR / "workbench.lock").open("a+b") as handle:
        if not handle.tell():
            handle.write(b"\0")
            handle.flush()
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise WorkspaceError(
                "Another research command or workbench owns this project"
            ) from None
        yield


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
        ev.SessionCreated(project_id=ws.project_id, title=redact(title)),
        session_id=session_id,
        actor=Actor.USER,
    )
    return session_id


def default_permissions(
    ws: Workspace,
    *,
    allow: list[PermissionClass],
    servers: list[ServerConfig],
    ask: Asker | None = None,
) -> PermissionPolicy:
    """What starting a BioQuorum server implies the user has agreed to.

    Reading inside the project and its data dirs, and running Python, are granted for the
    project. Configured MCP servers count as consented to. Everything else (network,
    connectors) needs an explicit ``--allow``. The MCP host usually adds its own per-call
    confirmation on top.
    """
    policy = PermissionPolicy(ask)
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
    lock_hash = None
    if kernel_factory is None:
        python_executable, lock_hash = project_environment(
            ws.root, use_pixi=bool(ws.config.get("kernel", {}).get("pixi", False))
        )
        # A read-only mount needs an existing file; without one, kernel code could create
        # the config itself and plant an MCP server command for the next start.
        (ws.root / CONFIG_FILE).touch(exist_ok=True)
        spec = ws.kernel_sandbox()
        kernel_factory = jupyter_factory(
            ws.root,
            sandbox=spec,
            require_isolation=ws.requires_sandbox(spec),
            python_executable=python_executable,
            observe_lineage=bool(ws.config.get("kernel", {}).get("lineage", False)),
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
        env_lock_hash=lock_hash,
        outbound_proxy=ws.outbound_proxy(),
    )
