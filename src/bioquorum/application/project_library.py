"""Discover and initialize local workbench projects without indexing session contents."""

import json
import tomllib
from pathlib import Path
from shutil import rmtree

from bioquorum.application import workspace
from bioquorum.core.enums import Classification
from bioquorum.core.ids import is_id, new_id


def library_root() -> Path:
    return Path.home() / workspace.STATE_DIR / "projects"


def project_path(key: str) -> Path:
    root = library_root() / key
    if (
        not is_id(key)
        or root.is_symlink()
        or not (root / workspace.STATE_DIR / "project.json").is_file()
    ):
        raise LookupError("Project not found")
    return root


def entries(current: workspace.Workspace) -> list[dict[str, str]]:
    rows = [{"name": current.name, "href": "/", "classification": current.classification.value}]
    library = library_root()
    if library.is_dir():
        for path in sorted(library.iterdir()):
            if not is_id(path.name) or path.is_symlink() or path.resolve() == current.root:
                continue
            meta = path / workspace.STATE_DIR / "project.json"
            if meta.is_file():
                info = json.loads(meta.read_text(encoding="utf-8"))
                rows.append(
                    {
                        "name": info["name"],
                        "classification": info["classification"],
                        "href": f"/projects/{path.name}/",
                    }
                )
    return rows


def create(name: str, classification: Classification) -> dict[str, str]:
    name = name.strip()
    if not name or len(name) > 160:
        raise ValueError("Enter a project name (1-160 characters)")
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
        workspace.init_project(path, name=name, classification=classification)
    except Exception:
        rmtree(path)
        raise
    return {"name": name, "classification": classification.value, "href": f"/projects/{key}/"}
