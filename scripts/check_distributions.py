"""Check wheel and sdist members before publication."""

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN = {".hypothesis", ".import_linter_cache", ".git", ".venv", "__pycache__", ".env"}
SDIST_ROOT = {
    ".gitignore",
    "PKG-INFO",
    "README.md",
    "LICENSE",
    "THIRD_PARTY.md",
    "pyproject.toml",
    "uv.lock",
}


def check_member(name: str, content: bytes, *, wheel: bool, private_paths: list[bytes]) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or FORBIDDEN.intersection(path.parts) or ".." in path.parts:
        raise ValueError(f"Unexpected archive member {name}")
    if wheel:
        allowed = path.parts[0] == "bioquorum" or path.parts[0].endswith(".dist-info")
    else:
        relative = PurePosixPath(*path.parts[1:])
        allowed = (
            relative.as_posix() in SDIST_ROOT
            or relative.parts[:2] == ("src", "bioquorum")
            or relative.parts[:1] == ("bundle-spec",)
        )
    if not allowed or any(marker in content for marker in private_paths):
        raise ValueError(f"Unexpected file or build-machine path in {name}")


def main(directory: Path) -> None:
    (wheel,) = directory.glob("*.whl")
    (sdist,) = directory.glob("*.tar.gz")
    private = [str(path).encode() for path in (Path.home(), Path.cwd()) if path != Path("/")]
    private.extend([b"/Users/", b"/home/", b"C:\\Users\\"])
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            check_member(name, archive.read(name), wheel=True, private_paths=private)
    with tarfile.open(sdist) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile() or (stream := archive.extractfile(member)) is None:
                raise ValueError(f"Unexpected archive entry {member.name}")
            check_member(member.name, stream.read(), wheel=False, private_paths=private)
    print(f"Checked {wheel.name} and {sdist.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    main(parser.parse_args().directory)
