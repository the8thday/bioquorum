"""Researcher settings shared by the workbench and the next CLI invocation."""

import asyncio
import copy
import os
import re
from dataclasses import replace
from typing import Any

from pydantic import BaseModel, ConfigDict

from bioquorum.application import model_setup
from bioquorum.application.controls import ConflictError, ResearchControls
from bioquorum.application.environment import package_inventory
from bioquorum.application.workspace import (
    RECOMMENDED,
    Workspace,
    WorkspaceError,
    default_permissions,
    profile_settings,
    settings_overlay,
)
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, ModelRole, PermissionClass
from bioquorum.models.routing import Router
from bioquorum.policy.classification import allows
from bioquorum.policy.secrets import redact
from bioquorum.runtime.sandbox import IsolationUnavailable
from bioquorum.tools.context import ToolContext
from bioquorum.tools.invoke import invoke
from bioquorum.tools.spec import ToolResult, ToolSpec

FLAGS = (
    "binding",
    "skills",
    "program_context",
    "project_notes",
    "literature_subtasks",
    "subtasks",
    "specialists",
    "source_to_kernel",
    "vision",
    "evidence_skills",
    "domain_skills",
    "first_pass",
    "coverage",
    "references",
)
NUMBERS = ("binding_retries", "compact_at_bytes", "budget_tokens", "budget_usd", "budget_seconds")
RATES = ("input_usd_per_million", "output_usd_per_million", "context_window")


class SettingsEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str | None
    patch: dict[str, dict[str, Any]]


def _grants(ws: Workspace):
    original = replace(ws, config={**ws.config, "permissions": {}})
    return default_permissions(original, allow=[], servers=ws.mcp_servers()).grants()


def _environment(ws: Workspace) -> dict:
    try:
        isolation = ws.kernel_isolation().value
    except (IsolationUnavailable, WorkspaceError):
        isolation = "unavailable"
    network = {"none": "unrestricted", "unavailable": "unavailable", "bwrap+net": "enabled"}
    if isolation not in network:
        network[isolation] = "enabled" if ws.kernel_network() else "blocked"
    return {
        "isolation": isolation,
        "network": network[isolation],
        **package_inventory(ws.root, use_pixi=bool(ws.config.get("kernel", {}).get("pixi"))),
    }


def project_state(controls: ResearchControls) -> tuple[str | None, dict]:
    ws = controls.ws
    revision = None
    checks = {}
    config = copy.deepcopy(ws.base_config) if ws.base_config is not None else ws.config
    for event in controls.store.read(
        None, types=[ev.ProjectSettingsChanged.event_type, ev.ConnectorChecked.event_type]
    ):
        data = event.payload
        if data["project_id"] != ws.project_id:
            continue
        if event.type == ev.ConnectorChecked.event_type:
            checks[data["server"]] = data
        else:
            revision = event.id
            for name in data["patch"].get("extensions", {}).get("connectors", {}):
                checks.pop(name, None)
            ws.classification = Classification(
                data["patch"].get("project", {}).get("classification", ws.classification)
            )
            config = settings_overlay(config, data["patch"])
    ws.config = config
    return revision, checks


def snapshot(controls: ResearchControls) -> dict:
    from bioquorum.application.model_eval import results

    ws = controls.ws
    revision, checks = project_state(controls)
    models = ws.models_config()
    options = model_setup.choices(models, ws.classification)
    candidates = list(models.providers.values())
    if not models.roles.get(ModelRole.WORKER) and model_setup.OPENAI_MODEL in options:
        candidates.append(model_setup.provider(models, "openai"))
    refs = sorted(
        {p.api_key for p in candidates if re.fullmatch(r"env:[A-Za-z_][A-Za-z_0-9]*", p.api_key)}
    )
    configured = Router(models).worker_options(ws.classification)
    agent = ws.agent_options()
    values = {k: getattr(agent, k) for k in (*FLAGS, "binding_retries", "compact_at_bytes")}
    values.update(
        literature_tools=list(agent.literature_tools),
        subtask_tools=list(agent.subtask_tools),
        subtask_parallelism=agent.subtask_parallelism,
        plan_default=ws.config.get("agent", {}).get("plan_default", False),
        budget_tokens=agent.budget.tokens,
        budget_seconds=agent.budget.seconds,
        budget_usd=agent.budget.usd,
    )
    revoked = ws.config.get("permissions", {}).get("revoked", [])
    return {
        "revision": revision,
        "profile": {
            "name": ws.profile_name,
            "recommended_available": RECOMMENDED is not None,
            "presets": {
                "minimal": profile_settings("minimal"),
                "recommended": profile_settings("recommended") if RECOMMENDED is not None else {},
            },
        },
        "example": controls.demo,
        "providers": {
            p.name: {
                "kind": p.kind,
                "allowed": ws.classification in p.allowed_classifications,
            }
            for p in models.providers.values()
        },
        "local_setup": model_setup.starter(models, ws.classification, "ollama:example"),
        "project": {
            "name": ws.name,
            "instructions": ws.config.get("project", {}).get("instructions", ""),
            "classification": ws.classification.value,
            "data_dirs": [str(d) for d in ws.data_dirs],
        },
        "keys": [
            {"reference": ref, "state": "set" if os.environ.get(ref[4:]) else "missing"}
            for ref in refs
        ],
        "agent": values,
        "model_evaluation": results(ws, controls.store),
        "roles": {role.value: ws.config.get("roles", {}).get(role.value, []) for role in ModelRole},
        "models": {
            spec: {
                "allowed": allowed,
                "configured": spec in configured,
                **{k: ws.config.get("models", {}).get(spec, {}).get(k) for k in RATES},
            }
            for spec, allowed in options.items()
        },
        "environment": _environment(ws),
        "permissions": [
            {
                "permission": g.permission.value,
                "target": g.target,
                "scope": g.scope.value,
                "revoked": [g.permission.value, g.target] in revoked,
            }
            for g in _grants(ws)
        ],
        "connectors": [
            {
                "name": s.name,
                "mode": "replay" if s.replay else "live",
                "clearance": s.clearance.value if s.clearance else "local",
                "allowed": allows(ws.classification, s.clearance),
                "status": checks.get(s.name, {}).get("status", "unchecked"),
                "tools": checks.get(s.name, {}).get("tools"),
            }
            for s in ws.mcp_servers()
        ],
    }


def _validate(ws: Workspace, patch: dict[str, dict[str, Any]]) -> dict:
    if not patch or patch.keys() - {
        "agent",
        "permissions",
        "roles",
        "models",
        "profile",
        "project",
    }:
        raise ValueError("Unsupported settings section")
    if "profile" in patch and set(patch["profile"]) != {"name"}:
        raise ValueError("Unknown profile setting")
    details = patch.get("project", {})
    if details.keys() - {"instructions", "display_name", "classification"}:
        raise ValueError("Unknown project setting")
    if "classification" in details:
        ws = replace(ws, classification=Classification(details["classification"]))
    for key, text in details.items():
        if not isinstance(text, str) or redact(text) != text:
            raise ValueError("Invalid project text")
        if key == "display_name" and not 1 <= len(text.strip()) <= 160:
            raise ValueError("Invalid project name")
        if key == "instructions" and len(text) > 6000:
            raise ValueError("Project instructions are too long")
        details[key] = text.strip()
    models = ws.models_config()
    options = Router(models).worker_options(ws.classification)
    agent = patch.get("agent", {})
    if agent.keys() - {
        *FLAGS,
        *NUMBERS,
        "plan_default",
        "literature_tools",
        "subtask_tools",
        "subtask_parallelism",
    }:
        raise ValueError("Unsupported agent setting")
    if any(type(agent[k]) is not bool for k in (*FLAGS, "plan_default") if k in agent):
        raise ValueError("Expected a switch")
    if "literature_tools" in agent:
        names = agent["literature_tools"]
        if not isinstance(names, list) or any(
            not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", n) for n in names
        ):
            raise ValueError("Expected a list of tool names")
    for role, values in patch.get("roles", {}).items():
        ModelRole(role)
        if role == "reviewer" and values == "auto":
            continue
        chain = [values] if isinstance(values, str) else values
        if not isinstance(chain, list) or not chain or not all(isinstance(s, str) for s in chain):
            raise ValueError("Select an allowed configured model")
        for spec in chain:
            if options.get(spec):
                continue
            if not model_setup.starter(models, ws.classification, spec):
                raise ValueError("Select an allowed configured model")
            name = spec.partition(":")[0]
            if name not in models.providers:
                patch.setdefault("providers", {})[name] = {"kind": name}
            options[spec] = True
    for model, rates in patch.get("models", {}).items():
        if model not in options or not isinstance(rates, dict) or rates.keys() - set(RATES):
            raise ValueError("Unknown model setting")
    if "permissions" in patch:
        permissions = patch["permissions"]
        pairs = [[g.permission.value, g.target] for g in _grants(ws)]
        if (
            set(permissions) != {"revoked"}
            or not isinstance(permissions["revoked"], list)
            or any(v not in pairs for v in permissions["revoked"])
        ):
            raise ValueError("Unknown permission")
    config = settings_overlay(ws.config, patch)
    candidate = replace(ws, config=config)
    if "classification" in details:
        candidate.kernel_network()
    candidate.agent_options()
    candidate.models_config()
    return config


def save(controls: ResearchControls, edit: SettingsEdit) -> dict:
    controls.ensure_idle()
    if controls.demo:
        raise ValueError("The offline example uses fixed settings")
    ws = controls.ws
    patch = dict(edit.patch)
    try:
        if "profile" in patch:
            preset = profile_settings(patch["profile"].get("name", ""))
            if preset and patch.get("agent"):
                raise ValueError("Choose either a preset or custom switches")
            if preset:
                patch["agent"] = {**preset, "subtasks": False, "specialists": False}
        elif ws.profile_name != "custom" and patch.get("agent", {}).keys() & (
            profile_settings(ws.profile_name).keys() | {"subtasks", "specialists"}
        ):
            patch["profile"] = {"name": "custom"}
        if set(patch) == {"connection"}:
            patch = model_setup.connection_patch(
                ws.models_config(), ws.classification, patch["connection"]
            )
            config = settings_overlay(ws.config, patch)
            replace(ws, config=config).models_config()
        else:
            config = _validate(ws, patch)
    except WorkspaceError as exc:
        if "classification" in patch.get("project", {}):
            raise ValueError(str(exc)) from None
        raise ValueError("Invalid project settings") from None
    except (ValueError, TypeError):
        raise ValueError(
            "Invalid settings. Check the selected models, tool names and numeric limits."
        ) from None
    try:
        controls.store.append(
            ev.ProjectSettingsChanged(
                project_id=ws.project_id, prior_revision=edit.revision, patch=patch
            ),
            actor=Actor.USER,
            session_id=None,
        )
    except ValueError:
        raise ConflictError("Project settings changed. Reopen settings and try again.") from None
    ws.config = config
    ws.name = config.get("project", {}).get("display_name", ws.name)
    return snapshot(controls)


class ProbeArgs(BaseModel):
    pass


async def reconnect(controls: ResearchControls, sid: str, name: str) -> dict:
    controls.require_session(sid)
    controls.ensure_idle()
    ws = controls.ws
    server = next((s for s in ws.mcp_servers() if s.name == name), None)
    if not server or controls.demo:
        raise LookupError("Connector unavailable")
    count = None

    async def probe(args, ctx):
        nonlocal count
        try:
            async with asyncio.timeout(15), ws.mcp_connection(server) as connection:
                count = len(await connection.list_tools())
        except Exception:
            return ToolResult.text("Connector unavailable", is_error=True)
        return ToolResult.text(f"{count} tools available")

    tool = ToolSpec(
        name="check_connector",
        description="Discover configured tools",
        handler=probe,
        args_model=ProbeArgs,
        permission=PermissionClass.EXTERNAL_MCP,
        classification_floor=server.clearance,
        target=lambda args, ctx: f"mcp:{name}",
    )
    ctx = ToolContext(
        store=controls.store,
        session_id=sid,
        project_id=ws.project_id,
        classification=ws.classification,
        actor=Actor.USER,
        permissions=default_permissions(ws, servers=[server], allow=[]),
    )
    result = await invoke(tool, {}, ctx)
    controls.store.append(
        ev.ConnectorChecked(
            project_id=ws.project_id,
            server=name,
            status="unavailable" if result.is_error else "reachable",
            tools=count,
        ),
        actor=Actor.USER,
        session_id=None,
    )
    return snapshot(controls)
