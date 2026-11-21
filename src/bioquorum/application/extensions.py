"""Project extension registration and catalog, shared with the normal research runtime."""

import base64
import re
from pathlib import Path
from shutil import rmtree
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from bioquorum.application.controls import ConflictError, ResearchControls
from bioquorum.application.settings import project_state
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.ids import new_id
from bioquorum.policy.classification import allows
from bioquorum.policy.secrets import public_url, redact
from bioquorum.skills.loader import MAX_SKILLS
from bioquorum.skills.package import unpack


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str | None
    action: Literal[
        "install_skill",
        "enable_skill",
        "disable_skill",
        "remove_skill",
        "add_connector",
        "enable_connector",
        "disable_connector",
        "remove_connector",
    ]
    name: str = Field(default="", max_length=64)
    filename: str = Field(default="", max_length=255)
    content: str = Field(default="", max_length=1_398_104)
    url: str = Field(default="", max_length=2048)
    command: str = Field(default="", max_length=128)
    args: list[str] = Field(default_factory=list, max_length=24)


def snapshot(controls: ResearchControls) -> dict:
    ws = controls.ws
    revision, checks = project_state(controls)
    library = ws.config.get("extensions", {})
    skills = []
    for name, skill in ws.project_skills(include_disabled=True).items():
        entry = library.get("skills", {}).get(name) or {}
        skills.append(
            dict(
                name=name,
                description=redact(skill.description),
                managed=bool(entry.get("version")),
                enabled=entry.get("enabled", True),
            )
        )
    connectors = []
    for server in ws.mcp_servers(include_disabled=True):
        entry = library.get("connectors", {}).get(server.name) or {}
        check = checks.get(server.name, {})
        connectors.append(
            dict(
                name=server.name,
                mode="Replay" if server.replay else "HTTPS" if server.url else "stdio",
                managed=bool(entry.get("config")),
                enabled=entry.get("enabled", True),
                status=check.get("status", "unchecked"),
                tools=check.get("tools"),
                allowed=allows(ws.classification, server.clearance),
                clearance=server.clearance.value if server.clearance else "local",
            )
        )
    return dict(
        revision=revision,
        example=controls.demo,
        project=ws.name,
        skills=skills,
        skills_enabled=ws.agent_options().skills or ws.agent_options().evidence_skills,
        connectors=connectors,
    )


def _connector(edit: Edit) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", edit.name) or redact(edit.name) != edit.name:
        raise ValueError("Use a connector name of up to 32 letters, digits, underscores or hyphens")
    if bool(edit.url) == bool(edit.command):
        raise ValueError("Choose an HTTPS endpoint or an installed local command")
    if edit.url:
        parts = urlsplit(edit.url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or public_url(edit.url) != edit.url
            or redact(edit.url) != edit.url
            or any(c.isspace() for c in edit.url)
            or "\\" in edit.url
            or edit.args
        ):
            raise ValueError("Use an HTTPS endpoint without credentials, query or fragment")
        return {"url": edit.url, "clearance": "public"}
    if (
        not re.fullmatch(r"[\w.+-]+", edit.command)
        or any(
            len(arg) > 200
            or not re.fullmatch(r"[\w./=:@+-]+", arg)
            or re.search("token|password|secret|api.key", arg, re.I)
            for arg in edit.args
        )
        or redact(" ".join([edit.command, *edit.args])) != " ".join([edit.command, *edit.args])
    ):
        raise ValueError("Use an installed executable and simple, credential-free arguments")
    return {"command": edit.command, "args": edit.args, "isolated": True, "clearance": "local"}


def save(controls: ResearchControls, edit: Edit) -> dict:
    ws = controls.ws
    controls.ensure_idle()
    if controls.demo:
        raise ValueError("Open a research project to customize its extensions")
    state = snapshot(controls)
    if edit.revision != state["revision"]:
        raise ConflictError("The project changed. Reload the list and try again.")
    group = "skills" if edit.action.endswith("_skill") else "connectors"
    entries = {s["name"]: s for s in state[group]}
    name = edit.name
    files: dict[str, bytes] = {}
    root: Path | None = None
    patch: dict = {}
    change: dict | None
    if edit.action == "install_skill":
        try:
            raw = base64.b64decode(edit.content, validate=True)
        except ValueError:
            raise ValueError("Invalid file encoding") from None
        name, files = unpack(edit.filename, raw)
        if any(redact(data.decode()) != data.decode() for data in files.values()):
            raise ValueError("Remove credentials before importing a skill")
        if name in entries:
            raise ValueError("A skill with this name is already installed")
        if len(entries) >= MAX_SKILLS:
            raise ValueError("Project skill catalog is full")
        version = new_id()
        root = ws.state_dir / "installed-skills" / version
        change = {"version": version, "enabled": True}
        patch["agent"] = {"skills": True}
    elif edit.action == "add_connector":
        if name in entries:
            raise ValueError("A connector with this name already exists")
        change = {"config": _connector(edit), "enabled": True}
    else:
        if name not in entries:
            raise LookupError("Extension not found")
        if edit.action.startswith("remove"):
            if not entries[name]["managed"]:
                raise ValueError("This extension is defined on disk. Disable it for this project.")
            change = None
        else:
            change = {"enabled": edit.action.startswith("enable")}
            if group == "skills" and change["enabled"]:
                patch["agent"] = {"skills": True}
    patch["extensions"] = {group: {name: change}}
    store_dir = ws.state_dir.resolve() / "installed-skills"
    try:
        if root is not None:
            if root.parent.is_symlink() or root.parent.resolve() != store_dir:
                raise ValueError("The project skill store must be a local directory")
            (root / name).mkdir(parents=True)
            for member, data in files.items():
                target = root / name / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        try:
            controls.store.append(
                ev.ProjectSettingsChanged(
                    project_id=ws.project_id, prior_revision=edit.revision, patch=patch
                ),
                actor=Actor.USER,
                session_id=None,
            )
        except ValueError:
            raise ConflictError("The project changed. Reload the list and try again.") from None
    except Exception:
        if root is not None and not root.parent.is_symlink() and root.parent.resolve() == store_dir:
            rmtree(root, ignore_errors=True)
        raise
    return snapshot(controls)
