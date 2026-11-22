"""Load bounded, project-local Markdown definitions using the Skill file reader."""

from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field

from bioquorum.core.enums import PermissionClass
from bioquorum.skills.loader import MAX_SKILLS, NAME, _header, _open


class Specialist(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(max_length=64)
    description: str = Field(min_length=1, max_length=512)
    tools: list[str] = Field(max_length=32)
    permissions: list[PermissionClass] = Field(max_length=6)
    model: str | None = Field(default=None, max_length=128)
    instructions: str = Field(min_length=1, max_length=8192)


def load_specialists(directory: Path) -> dict[str, Specialist]:
    result = {}
    if not directory.is_dir() or directory.is_symlink() or directory.resolve() != directory:
        return result
    for path in sorted(directory.glob("*.md")):
        if len(result) == MAX_SKILLS:
            break
        if not NAME.fullmatch(path.stem):
            continue
        try:
            with _open(directory, PurePosixPath(path.name)) as file:
                metadata = _header(file)
                profile = Specialist(**metadata, instructions=file.read().decode("utf-8").strip())
            if profile.name == path.stem:
                result[profile.name] = profile
        except (ValueError, TypeError, OSError, yaml.YAMLError):
            continue
    return result
