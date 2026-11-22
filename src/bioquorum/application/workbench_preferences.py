"""Presentation preferences shared by local workbench launches."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inbox_notifications: bool = False
    reading_size: Literal["standard", "large"] = "standard"
    initial_panel: Literal["Evidence", "Artifacts", "Activity", "Usage", "Context"] = "Activity"


def preferences_path() -> Path:
    return Path.home() / ".bioquorum" / "workbench-ui.json"


def read() -> Preferences:
    path = preferences_path()
    if not path.exists():
        return Preferences()
    return Preferences.model_validate_json(path.read_bytes())


def save(value: Preferences) -> None:
    path = preferences_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        staged = Path(temp.name)
    try:
        staged.write_text(value.model_dump_json() + "\n", encoding="utf-8")
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
