"""Discover and initialize local workbench projects without indexing session contents."""

import json
import tomllib
from pathlib import Path
from shutil import rmtree

from bioquorum.application import workspace
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification
from bioquorum.core.ids import is_id, new_id
from bioquorum.policy.secrets import redact
from bioquorum.storage.db import library_summary
from bioquorum.storage.events import EventStore


def library_root() -> Path:
    return Path.home() / workspace.STATE_DIR / "projects"


def _directory(trashed: bool) -> Path:
    library = library_root()
    return library / ".trash" if trashed else library


def project_path(key: str, *, trashed: bool = False) -> Path:
    parent = _directory(trashed)
    root = parent / key
    if (
        parent.is_symlink()
        or not is_id(key)
        or root.is_symlink()
        or not (root / workspace.STATE_DIR / "project.json").is_file()
    ):
        raise LookupError("Project not found")
    return root


def entries(current: workspace.Workspace, *, trashed: bool = False) -> list[dict]:
    rows = (
        []
        if trashed
        else [{"name": current.name, "href": "/", "classification": current.classification.value}]
    )
    library = _directory(trashed)
    if not library.is_dir() or library.is_symlink():
        return rows
    for path in sorted(library.iterdir()):
        if not is_id(path.name) or path.is_symlink() or path.resolve() == current.root:
            continue
        state = path / workspace.STATE_DIR
        try:
            info = json.loads((state / "project.json").read_text(encoding="utf-8"))
            if not all(isinstance(info[k], str) for k in ("project_id", "name", "classification")):
                continue
            summary = library_summary(state / "bioquorum.db", info["project_id"])
            rows.append(
                {
                    **(summary or {"name": info["name"], "classification": info["classification"]}),
                    "key": path.name,
                    "href": f"/projects/{path.name}/",
                }
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return rows


def move(key: str, *, restore: bool, current: workspace.Workspace) -> None:
    source = project_path(key, trashed=restore)
    if current.root.is_relative_to(source.resolve()):
        raise ValueError("The workbench startup directory cannot be moved")
    dest = _directory(not restore)
    if dest.is_symlink():
        raise ValueError("Invalid project library directory")
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = dest / key
    try:
        with workspace.project_lock(source):
            if target.exists() or target.is_symlink():
                raise ValueError("A project already occupies this location")
            source.rename(target)
    except workspace.WorkspaceError as exc:
        raise ValueError(str(exc)) from None
    except OSError:
        raise ValueError(
            "Could not move the project. Close other programs using its files."
        ) from None


def create(name: str, classification: Classification, instructions: str = "") -> dict[str, str]:
    name = name.strip()
    if not name or len(name) > 160 or redact(name) != name:
        raise ValueError("Enter a project name (1-160 characters) without credentials")
    if len(instructions) > 6000 or redact(instructions) != instructions:
        raise ValueError("Enter instructions of up to 6000 characters without credentials")
    library = library_root()
    library.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = new_id()
    path = library / key
    path.mkdir(mode=0o700)
    try:
        global_path = workspace.global_config_path()
        defaults = (
            tomllib.loads(global_path.read_text(encoding="utf-8")) if global_path.is_file() else {}
        )
        config = "[kernel]\nmacos_sandbox = true\n"
        if classification == Classification.PUBLIC and not defaults.get("roles", {}).get("worker"):
            if "openai" not in defaults.get("providers", {}):
                config += '\n[providers.openai]\napi_key = "env:OPENAI_API_KEY"\n'
            config += '\n[roles]\nworker = "openai:gpt-6.1-sol"\n'
            if not defaults.get("roles", {}).get("reviewer"):
                config += 'reviewer = "openai:gpt-6.1-sol"\n'
        (path / workspace.CONFIG_FILE).write_text(config, encoding="utf-8")
        ws = workspace.init_project(path, name=name, classification=classification)
        if instructions.strip():
            store = EventStore(ws.database())
            try:
                store.append(
                    ev.ProjectSettingsChanged(
                        project_id=ws.project_id,
                        prior_revision=None,
                        patch={"project": {"instructions": instructions.strip()}},
                    ),
                    actor=Actor.USER,
                    session_id=None,
                )
            finally:
                store.db.engine.dispose()
    except Exception:
        rmtree(path)
        raise
    return {"name": name, "classification": classification.value, "href": f"/projects/{key}/"}
