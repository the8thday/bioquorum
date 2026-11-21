"""Import the text portion of a local Agent Skills package."""

import io
import stat
import zipfile
import zlib
from pathlib import PurePosixPath

import yaml

from bioquorum.skills.loader import MAX_TEXT, NAME, _header, is_resource

MAX_PACKAGE = 1024 * 1024


def unpack(filename: str, raw: bytes) -> tuple[str, dict[str, bytes]]:
    if not raw or len(raw) > MAX_PACKAGE:
        raise ValueError("Choose a skill package of at most 1 MiB")
    if filename == "SKILL.md":
        files = {"SKILL.md": raw}
    elif filename.lower().endswith((".zip", ".skill")):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > 256:
                    raise ValueError("Too many files in the skill package")
                roots = [
                    PurePosixPath(i.filename).parent
                    for i in entries
                    if PurePosixPath(i.filename).name == "SKILL.md"
                    and not i.filename.startswith("__MACOSX/")
                ]
                if len(roots) != 1:
                    raise ValueError("The package must contain one SKILL.md")
                files = {}
                total = 0
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or "\\" in entry.filename
                        or stat.S_ISLNK(entry.external_attr >> 16)
                    ):
                        raise ValueError("Invalid path in the skill package")
                    if entry.is_dir() or not path.is_relative_to(roots[0]):
                        continue
                    key = path.relative_to(roots[0]).as_posix()
                    if key != "SKILL.md" and not is_resource(key):
                        continue
                    if key in files or len(key) > 200:
                        raise ValueError("Duplicate or oversized skill resources")
                    with archive.open(entry) as stream:
                        data = stream.read(MAX_TEXT + 1)
                    total += len(data)
                    if len(data) > MAX_TEXT or total > MAX_PACKAGE:
                        raise ValueError("Duplicate or oversized skill resources")
                    files[key] = data
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, zlib.error):
            raise ValueError("Could not read the skill archive") from None
    else:
        raise ValueError("Choose SKILL.md, a .skill file or a ZIP package")
    if "SKILL.md" not in files:
        raise ValueError("Missing SKILL.md file")
    seen = {key.casefold() for key in files}
    if len(seen) != len(files) or any(
        parent.as_posix().casefold() in seen
        for member in files
        for parent in PurePosixPath(member).parents
    ):
        raise ValueError("Conflicting paths in the skill package")
    main = files["SKILL.md"]
    if len(main) > MAX_TEXT:
        raise ValueError("SKILL.md exceeds 48 KiB")
    try:
        for data in files.values():
            data.decode("utf-8")
        reader = io.BytesIO(main)
        header = _header(reader)
        name, description = header.get("name"), header.get("description")
        if (
            not isinstance(name, str)
            or len(name) > 64
            or not NAME.fullmatch(name)
            or not isinstance(description, str)
            or not 0 < len(" ".join(description.split())) <= 1024
            or not reader.read().strip()
        ):
            raise ValueError("SKILL.md needs a valid name, description and instructions")
    except (UnicodeError, yaml.YAMLError, RecursionError):
        raise ValueError("Expected a UTF-8 skill with valid YAML frontmatter") from None
    return name, files
