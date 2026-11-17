"""Compress kernel output into something a model may read (ARCHITECTURE.md §6.2, principle 7).

The model never sees raw data. Text is capped; rich outputs become placeholders that point
at content-addressed copies; tabular objects are described by shape, dtypes, head and
describe. Everything here is duck-typed so ``runtime`` does not need pandas installed.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from typing import Any

from bioquorum.runtime.kernel import KernelOutput

DEFAULT_LIMIT = 4000
TRUNCATION_NOTE = "\n...[truncated {n} characters]"

# MIME types we show as text, in order of preference.
_TEXT_MIMES = ("text/markdown", "text/plain")
_IMAGE_MIMES = ("image/png", "image/jpeg", "image/svg+xml")


def truncate(text: str, limit: int = DEFAULT_LIMIT) -> str:
    """Keep the head and tail of ``text`` within ``limit`` characters, and say so."""
    if len(text) <= limit:
        return text
    keep = max(limit - 60, 0)
    head, tail = keep * 2 // 3, keep // 3
    cut = len(text) - head - tail
    return text[:head] + TRUNCATION_NOTE.format(n=cut) + "\n" + (text[-tail:] if tail else "")


def image_mime(out: KernelOutput) -> str | None:
    return next((m for m in _IMAGE_MIMES if m in out.data), None)


def output_text(out: KernelOutput) -> str | None:
    """Text form of one output; images return None (they are stored as refs)."""
    if out.text is not None:
        return out.text
    for mime in _TEXT_MIMES:
        if mime in out.data:
            return out.data[mime]
    return None


def summarize_outputs(
    outputs: Iterable[KernelOutput],
    *,
    limit: int = DEFAULT_LIMIT,
    image_labels: dict[int, str] | None = None,
) -> str:
    """One text block for the model. ``image_labels[i]`` replaces image output ``i``."""
    labels = image_labels or {}
    pieces: list[str] = []
    for i, out in enumerate(outputs):
        mime = image_mime(out)
        if mime is not None:
            pieces.append(labels.get(i, f"[{mime} output]"))
            continue
        text = output_text(out)
        if text:
            pieces.append(text.rstrip("\n"))
    return truncate("\n".join(pieces), limit)


def summarize_dataframe(df: Any, *, head: int = 5, limit: int = DEFAULT_LIMIT) -> str:
    """shape + dtypes + head + describe for anything that quacks like a DataFrame."""
    parts = [f"DataFrame shape={tuple(df.shape)}"]
    parts.append("dtypes:\n" + df.dtypes.to_string())
    parts.append(f"head({head}):\n" + df.head(head).to_string())
    with contextlib.suppress(ValueError, TypeError):  # e.g. no numeric columns
        parts.append("describe:\n" + df.describe().to_string())
    return truncate("\n".join(parts), limit)


def summarize_anndata(adata: Any, *, limit: int = DEFAULT_LIMIT) -> str:
    obs = ", ".join(map(str, list(adata.obs.columns)))
    var = ", ".join(map(str, list(adata.var.columns)))
    text = f"AnnData n_obs={adata.n_obs} n_vars={adata.n_vars}\nobs: [{obs}]\nvar: [{var}]"
    return truncate(text, limit)
