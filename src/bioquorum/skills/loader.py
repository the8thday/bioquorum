"""Read project-approved Agent Skills metadata and fetch bodies only when requested."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import yaml

from bioquorum.core.hashing import sha256_bytes

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
DOMAIN_SKILLS = {"omics-workflows", "target-literature", "sequence-structure", "chemical-records"}
EVIDENCE_SKILLS = {"omics-evidence", "target-evidence", "evidence-synthesis"}
MAX_SKILLS = 24
MAX_HEADER = 8192
MAX_TEXT = 48 * 1024
_TEXT_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml"}


def is_resource(resource: str) -> bool:
    member = PurePosixPath(resource)
    return (
        len(member.parts) >= 2
        and member.parts[0] in {"references", "assets"}
        and member.suffix.lower() in _TEXT_SUFFIXES
        and "\\" not in resource
        and ":" not in resource
        and not any(p.startswith(".") for p in member.parts)
    )


def _stamp(value: os.stat_result) -> tuple[int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_mtime_ns, value.st_size


def _open(root: Path, relative: PurePosixPath) -> BinaryIO:
    here = root
    if root.is_symlink() or root.resolve() != root:
        raise ValueError("Skill directory must be a local directory without symbolic links")
    for part in relative.parts:
        here = here / part
        if here.is_symlink():
            raise ValueError("Symbolic links are not skill resources")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        # Walk from the absolute anchor with no-follow descriptors. A pathname check alone
        # can be invalidated by an intermediate symlink swap.
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        anchor, *parts = here.parts
        parent = os.open(anchor, directory_flags)
        try:
            for part in parts[:-1]:
                child = os.open(part, directory_flags, dir_fd=parent)
                os.close(parent)
                parent = child
            leaf = os.open(parts[-1], flags, dir_fd=parent)
        finally:
            os.close(parent)
    else:
        leaf = os.open(here, flags)
    handle = os.fdopen(leaf, "rb")
    found = os.fstat(handle.fileno())
    if not stat.S_ISREG(found.st_mode) or found.st_size > MAX_TEXT or here.resolve() != here:
        handle.close()
        raise ValueError("Skill resource must be a bounded local text file")
    return handle


def _header(file: BinaryIO) -> dict:
    if file.readline(12).strip() != b"---":
        raise ValueError("SKILL.md must start with YAML frontmatter")
    parts: list[bytes] = []
    size = 0
    while size < MAX_HEADER:
        line = file.readline(MAX_HEADER - size + 1)
        if line.strip() == b"---":
            data = yaml.safe_load(b"".join(parts))
            if isinstance(data, dict):
                return data
            break
        if not line:
            break
        parts.append(line)
        size += len(line)
    raise ValueError("SKILL.md frontmatter is missing or too large")


@dataclass(frozen=True)
class SkillContent:
    text: str
    sha256: str
    resource: str


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    root: Path
    stamp: tuple[int, int, int, int]
    license: str = ""

    def load(self, resource: str | None = None) -> SkillContent:
        member = PurePosixPath(resource or "SKILL.md")
        if resource is not None and not is_resource(resource):
            raise ValueError("Use a relative text file in references/ or assets/")
        with _open(self.root, PurePosixPath("SKILL.md")) as main:
            if _stamp(os.fstat(main.fileno())) != self.stamp:
                raise ValueError("Skill descriptor changed. Start a new turn to reload it.")
            if resource is None:
                _header(main)
                raw = main.read(MAX_TEXT + 1)
            else:
                with _open(self.root, member) as asset:
                    raw = asset.read(MAX_TEXT + 1)
        if len(raw) > MAX_TEXT:
            raise ValueError("Skill resource exceeds the size limit")
        text = raw.decode("utf-8")
        if not text.strip():
            raise ValueError("Skill resource is empty")
        return SkillContent(text, sha256_bytes(raw), member.as_posix())


def builtin_catalog() -> dict[str, Skill]:
    return discover(Path(__file__).resolve().parent / "builtin")


def discover(directory: Path) -> dict[str, Skill]:
    """Only parse frontmatter. The model never receives undeclared skill bodies."""
    if not directory.is_dir() or directory.is_symlink() or directory.resolve() != directory:
        return {}
    skills = {}
    for root in sorted(directory.iterdir()):
        name = root.name
        if len(skills) >= MAX_SKILLS:
            break
        if len(name) > 64 or not NAME.fullmatch(name) or root.is_symlink() or not root.is_dir():
            continue
        try:
            with _open(root, PurePosixPath("SKILL.md")) as file:
                metadata = _header(file)
                desc = metadata.get("description")
                if metadata.get("name") != name or not isinstance(desc, str):
                    continue
                desc = " ".join(desc.split())
                if 0 < len(desc) <= 1024:
                    license = metadata.get("license")
                    skills[name] = Skill(
                        name,
                        desc,
                        root,
                        _stamp(os.fstat(file.fileno())),
                        license[:512] if isinstance(license, str) else "",
                    )
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return skills
