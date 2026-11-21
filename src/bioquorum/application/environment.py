"""Locate a provisioned, locked Python environment without installing it."""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

from bioquorum.core.hashing import env_lock_hash
from bioquorum.runtime.r_environment import r_spec_path


def project_environment(root: Path, *, use_pixi: bool) -> tuple[Path | None, str | None]:
    if not use_pixi:
        return None, None
    lock = root / "pixi.lock"
    prefix = root / ".pixi" / "envs" / "default"
    python = next((p for p in (prefix / "bin/python", prefix / "python.exe") if p.is_file()), None)
    if not lock.is_file() or python is None:
        raise ValueError(
            "The locked pixi environment is missing. Provision it with pixi install --locked."
        )
    return python, env_lock_hash(lock.read_bytes())


def package_inventory(root: Path, *, use_pixi: bool) -> dict:
    """Read package metadata without executing code from the project environment."""
    prefix = root / ".pixi/envs/default"
    lock = None
    python_available = True
    try:
        _, lock = project_environment(root, use_pixi=use_pixi)
    except ValueError:
        python_available = False
    packages = []
    if python_available:
        paths = [*prefix.glob("lib/python*/site-packages"), prefix / "Lib/site-packages"]
        dists = (
            metadata.distributions(path=[str(p) for p in paths])
            if use_pixi
            else metadata.distributions()
        )
        packages = sorted({(d.metadata["Name"], d.version) for d in dists if d.metadata["Name"]})
    r_ready = bool(use_pixi and lock and r_spec_path(prefix))
    r_packages = []
    if r_ready:
        for path in (prefix / "lib/R/library").glob("*/DESCRIPTION"):
            if not path.resolve().is_relative_to(prefix.resolve()):
                continue
            with path.open(encoding="utf-8", errors="replace") as stream:
                description = stream.read(128_000)
            name = re.search(r"^Package:[ \t]*([\w.]+)[ \t]*$", description, re.M)
            version = re.search(r"^Version:[ \t]*([\w.+-]+)[ \t]*$", description, re.M)
            if name and version:
                r_packages.append((name[1], version[1]))
    return {
        "packages": packages,
        "r_packages": sorted(r_packages),
        "r_available": r_ready,
        "lock_hash": lock,
    }
