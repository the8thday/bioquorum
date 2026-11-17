"""Hash and identity rules (ARCHITECTURE.md §5.6, ADR-008).

Everything that needs a hash goes through this module so the rules stay in one place.
Canonical serializers for heavy objects (DataFrame, AnnData) are *registered* from
``bioquorum.provenance`` so this module stays free of heavy imports.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple

ALGORITHM = "sha256"
_CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------- code & environment


def normalize_code(code: str) -> str:
    """Strip trailing whitespace per line, normalise newlines, end with exactly one newline."""
    lines = code.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    stripped = [line.rstrip() for line in lines]
    while stripped and stripped[-1] == "":
        stripped.pop()
    return "\n".join(stripped) + "\n"


def code_hash(code: str) -> str:
    return sha256_text(normalize_code(code))


def env_lock_hash(lock_content: str | bytes) -> str:
    """Hash of the environment lock file (pixi.lock) exactly as stored."""
    if isinstance(lock_content, str):
        lock_content = lock_content.encode("utf-8")
    return sha256_bytes(lock_content)


def run_key(code_hash_: str, env_lock_hash_: str | None, input_hashes: Iterable[str]) -> str:
    """Identity of a Run: code + environment + sorted inputs.

    Two runs with the same key must produce the same outputs; otherwise reproduction failed.
    ``env_lock_hash_`` may be None before pixi locking is enforced (ROADMAP v0.1); it is
    recorded as the literal string ``none`` so keys stay comparable.
    """
    inputs = "\n".join(sorted(set(input_hashes)))
    material = f"{code_hash_}\n{env_lock_hash_ or 'none'}\n{inputs}"
    return sha256_text(material)


# ----------------------------------------------------------------- objects


class ObjectHash(NamedTuple):
    hash: str
    weak: bool
    serializer: str


Serializer = Callable[[Any], bytes]

# Canonical DataFrame form (ARCHITECTURE.md §5.6): columns sorted by name, parquet with fixed
# options. Kept here as plain data so provenance/ (in-process) and runtime/ (kernel-side
# snippet) serialise identically without importing each other.
DATAFRAME_PARQUET_KWARGS: dict[str, Any] = {
    "engine": "pyarrow",
    "compression": "zstd",
    "compression_level": 3,
    "index": True,
}
_SERIALIZERS: list[tuple[str, Callable[[Any], bool], Serializer]] = []


def register_serializer(name: str, matches: Callable[[Any], bool], serialize: Serializer) -> None:
    """Register a canonical serializer. Later registrations take precedence."""
    _SERIALIZERS.insert(0, (name, matches, serialize))


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def hash_object(obj: Any) -> ObjectHash:
    """Hash an in-memory object via its canonical form.

    bytes/str/JSON-like values hash strongly. Registered serializers (parquet for DataFrame,
    h5ad for AnnData) hash strongly. Anything else falls back to pickle and is marked weak:
    weak hashes are recorded but never used to decide reproduction.
    """
    if isinstance(obj, bytes | bytearray | memoryview):
        return ObjectHash(sha256_bytes(bytes(obj)), False, "bytes")
    if isinstance(obj, str):
        return ObjectHash(sha256_text(obj), False, "text")
    for name, matches, serialize in _SERIALIZERS:
        if matches(obj):
            return ObjectHash(sha256_bytes(serialize(obj)), False, name)
    try:
        return ObjectHash(sha256_bytes(canonical_json(obj)), False, "json")
    except (TypeError, ValueError):
        pass
    return ObjectHash(sha256_bytes(pickle.dumps(obj, protocol=5)), True, "pickle")


# ----------------------------------------------------------------- content-addressed store


def cas_relpath(content_hash: str) -> Path:
    """Relative path inside a content-addressed store: ``ab/abcdef...``."""
    return Path(content_hash[:2]) / content_hash
