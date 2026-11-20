"""Named values declared in kernel code with ``bq.value()`` (ADR-014).

The kernel gets a small ``bq`` object at start and after every restart (:data:`PRELUDE`).
It is also registered as the module ``bq``, so ``import bq`` returns the same object.
It does not import bioquorum, so it works in any Python environment with ipykernel. A call
publishes one ``display_data`` carrying :data:`VALUE_MIME` (the structured value) and
``text/plain`` (so the model sees what it declared); ``tools/runs.py`` turns the structured
part into ``NamedValue`` entries on ``run_finished``.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from bioquorum.core.enums import OutputStream
from bioquorum.core.refs import MAX_VALUE_TEXT, VALUE_NAME_PATTERN, NamedValue
from bioquorum.runtime.kernel import KernelOutput

VALUE_MIME = "application/vnd.bioquorum.value+json"

PRELUDE = (
    r'''
def __bq_install():
    import math, re
    from IPython.display import publish_display_data

    name_ok = re.compile(__NAME__).fullmatch

    class BioQuorum:
        """BioQuorum helpers available in every kernel."""

        def value(self, name, value, unit=None, desc=None):
            """Declare a named scalar result. Answers cite it as {{name}}.

            value: int, float or short str (numpy scalars are converted). unit: e.g. "genes".
            desc: what the number is, in one sentence. Returns value unchanged."""
            if not isinstance(name, str) or not name_ok(name):
                raise ValueError(
                    "bq.value name must start with a letter and use only letters, digits "
                    "and underscores (max 64): %r" % (name,)
                )
            v = value
            item = getattr(v, "item", None)
            if callable(item) and getattr(v, "shape", None) == ():
                v = item()
            if isinstance(v, bool) or not isinstance(v, (int, float, str)):
                raise TypeError(
                    "bq.value takes an int, float or str, got %s; declare one number per "
                    "name (tables and figures are cited as artifacts)" % type(value).__name__
                )
            if isinstance(v, float) and not math.isfinite(v):
                raise ValueError("bq.value: %s is not finite" % name)
            if isinstance(v, str) and len(v) > __MAXTEXT__:
                raise ValueError("bq.value: text values are limited to __MAXTEXT__ characters")
            if unit is not None and (not isinstance(unit, str) or len(unit) > 40):
                raise ValueError("bq.value: unit must be a short string")
            if desc is not None and (not isinstance(desc, str) or len(desc) > 500):
                raise ValueError("bq.value: desc must be a string of at most 500 characters")
            if isinstance(v, int):
                v = int(v)  # plain int, not a subclass
            shown = repr(v) if isinstance(v, str) else str(v)
            text = "bq.value %s = %s%s" % (name, shown, " " + unit if unit else "")
            payload = {"name": name, "value": v, "unit": unit, "desc": desc}
            publish_display_data({__MIME__: payload, "text/plain": text})
            return value

        def __repr__(self):
            return "<bq: bq.value(name, value, unit=None, desc=None)>"

    return BioQuorum()


bq = __bq_install()
del __bq_install
__import__("sys").modules["bq"] = bq  # so a stray ``import bq`` returns the same object
'''.replace("__NAME__", repr(VALUE_NAME_PATTERN.strip("^$")))
    .replace("__MAXTEXT__", str(MAX_VALUE_TEXT))
    .replace("__MIME__", repr(VALUE_MIME))
)


def parse_value(out: KernelOutput) -> NamedValue | str | None:
    """The NamedValue carried by ``out``; an error text if it is malformed; None if the
    output is not a ``bq.value`` declaration."""
    raw = out.data.get(VALUE_MIME)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return "bq.value payload is not an object"
        return NamedValue.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        return f"bq.value payload rejected: {str(exc).splitlines()[0][:200]}"


def value_output(
    name: str, value: int | float | str, unit: str | None = None, desc: str | None = None
) -> KernelOutput:
    """The output a real kernel produces for ``bq.value(name, value, unit, desc)``; used by
    ``FakeKernel`` scripts in tests."""
    shown = repr(value) if isinstance(value, str) else str(value)
    payload = {"name": name, "value": value, "unit": unit, "desc": desc}
    return KernelOutput(
        stream=OutputStream.DISPLAY,
        data={
            VALUE_MIME: json.dumps(payload),
            "text/plain": f"bq.value {name} = {shown}{' ' + unit if unit else ''}",
        },
    )
