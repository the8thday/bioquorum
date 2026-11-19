"""Locate a provisioned, locked Python environment without installing it."""

from __future__ import annotations

from pathlib import Path

from bioquorum.core.hashing import env_lock_hash


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
