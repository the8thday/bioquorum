"""Provenance layer: content-addressed store, canonical hashing, manifests (ARCHITECTURE.md §6.3)."""

from bioquorum.provenance import serializers
from bioquorum.provenance.cas import ContentStore, IntegrityError, file_ref
from bioquorum.provenance.manifest import build_manifest

serializers.install()

__all__ = ["ContentStore", "IntegrityError", "build_manifest", "file_ref"]
