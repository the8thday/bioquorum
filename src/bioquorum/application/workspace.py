"""Composition root: turn a project directory into a ready ToolContext and registry.

Layout of a project::

    <root>/bioquorum.toml          # optional; [mcp.servers.<name>] etc.
    <root>/.bioquorum/project.json # project id, name, classification
    <root>/.bioquorum/bioquorum.db # event store
    <root>/.bioquorum/cas/         # content-addressed store

CLI and workbench share this composition so lower layers stay injectable.
"""

from __future__ import annotations

import copy
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


def settings_overlay(config: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Changing a role must leave its formerly selected model available in the picker."""
    merged = _merge(config, patch)
    if "roles" in patch or "models" in patch:
        models = dict(merged.get("models", {}))
        if "roles" in patch:
            for chain in ModelsConfig.from_dict(config).roles.values():
                for spec in chain:
                    models.setdefault(str(spec), {})
        for spec in patch.get("models", {}):
            models[spec] = {k: v for k, v in models[spec].items() if v is not None}
        merged["models"] = models
    return merged


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
    program_context: bool = False
    project_notes: bool = False
    literature_subtasks: bool = False
    specialists: bool = False
    subtasks: bool = False
    subtask_parallelism: int = 2
    subtask_tools: tuple[str, ...] = ()
    literature_tools: tuple[str, ...] = ()
    source_to_kernel: bool = False
    references: bool = False
    evidence_skills: bool = False
    domain_skills: bool = False
    first_pass: bool = False
    coverage: bool = False
    vision: bool = False

    @property
    def allows_references(self) -> bool:
        return self.references or self.first_pass


# The v0.8 confirmation reproduced the current profile on the six frozen cases (ADR-038).
RECOMMENDED: dict[str, Any] | None = {"binding": True}
MINIMAL = {
    "binding": False,
    "binding_retries": 0,
    "compact_at_bytes": None,
    "skills": False,
    "program_context": False,
    "project_notes": False,
    "literature_subtasks": False,
    "source_to_kernel": False,
    "references": False,
    "vision": False,
    "coverage": False,
    "first_pass": False,
    "evidence_skills": False,
    "plan_default": False,
}


def profile_settings(name: str) -> dict[str, Any]:
    if name == "minimal":
        return MINIMAL.copy()
    if name == "recommended" and RECOMMENDED is not None:
        return {**MINIMAL, **RECOMMENDED}
    if name == "custom":
        return {}
    raise WorkspaceError("Unknown or unevaluated research profile")


@dataclass
class Workspace:
    root: Path
    project_id: str
    name: str
    classification: Classification
    data_dirs: list[Path] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    base_config: dict[str, Any] | None = None

    def public_sources_enabled(self, *, standard_registry: bool = True) -> bool:
        # Injected registries pin the tool set for offline examples and controlled runs.
        default = standard_registry and self.classification == Classification.PUBLIC
        return self.config.get("connectors", {}).get("public_sources", default) is True

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    def database(self) -> Database:
        return Database.at(self.state_dir / "bioquorum.db")

    def cas(self) -> ContentStore:
        return ContentStore(self.state_dir / "cas")

    def project_skills(self, *, include_disabled: bool = False):
        from bioquorum.skills.loader import (
            DOMAIN_SKILLS,
            EVIDENCE_SKILLS,
            builtin_catalog,
            discover,
        )

        opts = self.agent_options()
        names = (EVIDENCE_SKILLS if opts.evidence_skills else set()) | (
            DOMAIN_SKILLS if opts.domain_skills else set()
        )
        catalog = {k: v for k, v in builtin_catalog().items() if k in names} if names else {}
        catalog.update(discover(self.state_dir / "skills"))
        for name, entry in self.config.get("extensions", {}).get("skills", {}).items():
            if not entry:
                continue
            if entry.get("version"):
                package = discover(self.state_dir / "installed-skills" / entry["version"])
                if name in package:
                    catalog[name] = package[name]
            if not include_disabled and not entry.get("enabled", True):
                catalog.pop(name, None)
        return catalog

    def mcp_servers(self, *, include_disabled: bool = False) -> list[ServerConfig]:
        servers = dict(self.config.get("mcp", {}).get("servers", {}))
        for name, entry in self.config.get("extensions", {}).get("connectors", {}).items():
            if not entry:
                continue
            if entry.get("config"):
                servers[name] = entry["config"]
            if not include_disabled and not entry.get("enabled", True):
                servers.pop(name, None)
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

    @property
    def profile_name(self) -> str:
        return self.config.get("profile", {}).get("name", "custom")

    def agent_options(self) -> AgentOptions:
        preset = profile_settings(self.profile_name)
        configured = self.config.get("agent", {})
        cfg = (
            {**configured, **preset} if self.profile_name == "minimal" else {**preset, **configured}
        )
        for flag in (
            "skills",
            "program_context",
            "project_notes",
            "literature_subtasks",
            "specialists",
            "subtasks",
            "source_to_kernel",
            "evidence_skills",
            "domain_skills",
            "first_pass",
            "coverage",
            "references",
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
        tools = cfg.get("subtask_tools", [])
        parallelism = cfg.get("subtask_parallelism", 2)
        if (
            not isinstance(tools, list)
            or len(tools) > 32
            or any(not isinstance(name, str) or not 1 <= len(name) <= 64 for name in tools)
        ):
            raise WorkspaceError("[agent] subtask_tools must list exact tool names")
        if self.profile_name != "minimal" and cfg.get("subtasks") and not tools:
            raise WorkspaceError("[agent] subtasks requires an explicit subtask_tools allowlist")
        if type(parallelism) is not int or not 1 <= parallelism <= 8:
            raise WorkspaceError("[agent] subtask_parallelism must be between 1 and 8")
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
            program_context=cfg.get("program_context", False),
            project_notes=cfg.get("project_notes", False),
            literature_subtasks=cfg.get("literature_subtasks", False),
            literature_tools=tuple(literature),
            specialists=self.profile_name != "minimal" and cfg.get("specialists", False),
            subtasks=self.profile_name != "minimal" and cfg.get("subtasks", False),
            subtask_tools=tuple(tools),
            subtask_parallelism=parallelism,
            source_to_kernel=cfg.get("source_to_kernel", False),
            evidence_skills=cfg.get("evidence_skills", False),
            domain_skills=self.profile_name != "minimal" and cfg.get("domain_skills", False),
            first_pass=cfg.get("first_pass", False),
            coverage=cfg.get("coverage", False),
            references=cfg.get("references", False),
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
        protected = [
            self.root / name for name in (CONFIG_FILE, ".git", ".pixi", "pixi.toml", "pixi.lock")
        ]
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
    root: Path,
    *,
    name: str | None = None,
    classification: Classification = Classification.INTERNAL,
    use_recommended: bool = True,
) -> Workspace:
    root = root.resolve()
    meta = root / STATE_DIR / "project.json"
    if meta.exists():
        raise WorkspaceError(f"already a BioQuorum project: {root}")
    ws = Workspace(
        root=root, project_id=new_id(), name=name or root.name, classification=classification
    )
    ws.state_dir.mkdir(parents=True, exist_ok=True)
    recommended = (
        profile_settings("recommended") if use_recommended and RECOMMENDED is not None else None
    )
    created = ev.ProjectCreated(
        project_id=ws.project_id,
        name=ws.name,
        root_dir=str(root),
        classification=classification,
        recommended_agent=recommended,
    )
    if recommended is not None:
        ws.config = {"profile": {"name": "recommended"}, "agent": recommended}
    EventStore(ws.database()).append(created, session_id=None, actor=Actor.USER)
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
    classification = Classification(meta["classification"])
    config: dict[str, Any] = {}
    global_config = global_config or global_config_path()
    if global_config.is_file():
        config = tomllib.loads(global_config.read_text(encoding="utf-8"))
    cfg = root / CONFIG_FILE
    if cfg.is_file():  # project settings override global ones
        config = _merge(config, tomllib.loads(cfg.read_text(encoding="utf-8")))
    store = EventStore(Database.at(root / STATE_DIR / "bioquorum.db"))
    try:
        for event in store.read(None, types=[ev.ProjectCreated.event_type]):
            if event.payload["project_id"] != meta["project_id"]:
                continue
            classification = Classification(event.payload["classification"])
            recommended = event.payload.get("recommended_agent")
            if recommended is not None:
                switches = config.get("agent", {})
                customized = any(
                    switches[k] != recommended[k] for k in switches.keys() & recommended.keys()
                )
                default_name = "custom" if customized else "recommended"
                config = _merge({"profile": {"name": default_name}, "agent": recommended}, config)
            break
        base_config = copy.deepcopy(config)
        for event in store.read(None, types=[ev.ProjectSettingsChanged.event_type]):
            if event.payload["project_id"] == meta["project_id"]:
                patch = event.payload["patch"]
                classification = Classification(
                    patch.get("project", {}).get("classification", classification)
                )
                config = settings_overlay(config, patch)
    finally:
        store.db.engine.dispose()
    project = config.get("project", {})
    data_dirs = [(root / d).resolve() for d in project.get("data_dirs", [])]
    return Workspace(
        root=root,
        project_id=meta["project_id"],
        name=project.get("display_name", meta["name"]),
        classification=classification,
        data_dirs=data_dirs,
        config=config,
        base_config=base_config,
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
    if ws.config.get("kernel", {}).get("pixi"):
        policy.grant(PermissionClass.EXECUTE, "r", scope, project_id=pid)
    for s in servers:
        policy.grant(PermissionClass.EXTERNAL_MCP, f"mcp:{s.name}", scope, project_id=pid)
    for perm in allow:
        policy.grant(perm, "*", PermissionScope.GLOBAL)
    for permission, target in ws.config.get("permissions", {}).get("revoked", []):
        policy.revoke(PermissionClass(permission), target)
    return policy


def tool_context(
    ws: Workspace,
    store: EventStore,
    session_id: str,
    permissions: PermissionPolicy,
    *,
    kernel_factory: KernelFactory | None = None,
    env_lock_hash: str | None = None,
) -> ToolContext:
    """``env_lock_hash`` describes a caller-supplied ``kernel_factory``; without one, the
    project's own environment and lock are used."""
    lock_hash = env_lock_hash
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
