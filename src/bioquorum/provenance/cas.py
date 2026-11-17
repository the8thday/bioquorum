"""Content-addressed store (ARCHITECTURE.md §5.6, §6.3).

Layout: ``<root>/<hash[:2]>/<hash>``. Writes go to a temp file, are hashed, then renamed,
so a blob on disk always matches its name. Reads verify by default.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from bioquorum.core.enums import DataKind
from bioquorum.core.hashing import cas_relpath, sha256_bytes, sha256_file
from bioquorum.core.refs import DataRef

URI_SCHEME = "cas://"


class IntegrityError(Exception):
    """A blob's content does not match its hash."""


class ContentStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, content_hash: str) -> Path:
        return self.root / cas_relpath(content_hash)

    def has(self, content_hash: str) -> bool:
        return self.path(content_hash).is_file()

    @staticmethod
    def uri(content_hash: str) -> str:
        return f"{URI_SCHEME}{content_hash}"

    # ------------------------------------------------------------- write

    def put_bytes(
        self, data: bytes, *, kind: DataKind = DataKind.OTHER, expect: str | None = None
    ) -> DataRef:
        digest = sha256_bytes(data)
        if expect is not None and expect != digest:
            raise IntegrityError(f"expected {expect}, got {digest}")
        target = self.path(digest)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        return DataRef(uri=self.uri(digest), content_hash=digest, size=len(data), kind=kind)

    def put_file(self, src: str | Path, *, kind: DataKind = DataKind.FILE) -> DataRef:
        src = Path(src)
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-")
        os.close(fd)
        shutil.copyfile(src, tmp)
        digest = sha256_file(tmp)
        target = self.path(digest)
        if target.exists():
            os.unlink(tmp)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, target)
        return DataRef(
            uri=self.uri(digest), content_hash=digest, size=target.stat().st_size, kind=kind
        )

    # ------------------------------------------------------------- read

    def get_bytes(self, content_hash: str, *, verify: bool = True) -> bytes:
        data = self.path(content_hash).read_bytes()
        if verify and sha256_bytes(data) != content_hash:
            raise IntegrityError(f"blob {content_hash} is corrupt")
        return data

    def get(self, ref: DataRef, *, verify: bool = True) -> bytes:
        return self.get_bytes(ref.content_hash, verify=verify)


def file_ref(path: str | Path, *, kind: DataKind = DataKind.FILE) -> DataRef:
    """A DataRef for a file in place (not copied into the store)."""
    p = Path(path).resolve()
    return DataRef(uri=p.as_uri(), content_hash=sha256_file(p), size=p.stat().st_size, kind=kind)
