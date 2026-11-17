"""Canonical serializers for heavy objects, registered into core.hashing (ARCHITECTURE.md §5.6).

core/ cannot import pandas or anndata, so the canonical forms live here and are registered
at import time of ``bioquorum.provenance``. Matchers check the type by module and name so
nothing heavy is imported until an object of that type actually appears.
"""

from __future__ import annotations

import io
import os
import tempfile
from typing import Any

from bioquorum.core import hashing


def _is_type(obj: Any, module_prefix: str, name: str) -> bool:
    cls = type(obj)
    return cls.__name__ == name and cls.__module__.split(".")[0] == module_prefix


def is_dataframe(obj: Any) -> bool:
    return _is_type(obj, "pandas", "DataFrame")


def is_anndata(obj: Any) -> bool:
    return _is_type(obj, "anndata", "AnnData")


def dataframe_bytes(df: Any) -> bytes:
    buf = io.BytesIO()
    df[sorted(df.columns, key=str)].to_parquet(buf, **hashing.DATAFRAME_PARQUET_KWARGS)
    return buf.getvalue()


def anndata_bytes(adata: Any) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".h5ad")
    os.close(fd)
    try:
        adata.write_h5ad(path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


_installed = False


def install() -> None:
    """Register serializers once. Idempotent."""
    global _installed
    if _installed:
        return
    hashing.register_serializer("parquet", is_dataframe, dataframe_bytes)
    hashing.register_serializer("h5ad", is_anndata, anndata_bytes)
    _installed = True
