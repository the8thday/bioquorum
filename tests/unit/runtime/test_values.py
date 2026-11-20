"""bq.value() in the kernel prelude, tested without a kernel by stubbing IPython's display."""

import json
import math
import sys
import types

import pytest

from bioquorum.core.enums import OutputStream
from bioquorum.core.refs import NamedValue
from bioquorum.runtime.kernel import KernelOutput
from bioquorum.runtime.values import PRELUDE, VALUE_MIME, parse_value, value_output


@pytest.fixture
def bq(monkeypatch):
    published: list[dict] = []
    display = types.ModuleType("IPython.display")
    display.publish_display_data = published.append  # type: ignore[attr-defined]
    ipython = types.ModuleType("IPython")
    ipython.display = display  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "IPython", ipython)
    monkeypatch.setitem(sys.modules, "IPython.display", display)
    monkeypatch.setitem(sys.modules, "bq", None)  # removed again after the test
    ns: dict = {}
    exec(PRELUDE, ns)
    assert "__bq_install" not in ns  # the prelude cleans up after itself
    return ns["bq"], published


def as_output(data: dict) -> KernelOutput:
    """What jupyter.py makes of a display_data message (JSON mimes arrive as objects)."""
    return KernelOutput(
        stream=OutputStream.DISPLAY,
        data={k: v if isinstance(v, str) else json.dumps(v) for k, v in data.items()},
    )


def test_value_publishes_structured_and_text(bq):
    obj, published = bq
    assert obj.value("n_de", 412, unit="genes", desc="DE genes at FDR 0.05") == 412
    (data,) = published
    assert data["text/plain"] == "bq.value n_de = 412 genes"
    nv = parse_value(as_output(data))
    assert nv == NamedValue(name="n_de", value=412, unit="genes", desc="DE genes at FDR 0.05")


def test_import_bq_returns_the_injected_object(bq):
    # Models often write ``import bq`` even when told it is predefined; that must not fail.
    obj, published = bq
    ns: dict = {}
    exec("import bq\nbq.value('n', 3)", ns)
    assert ns["bq"] is obj and len(published) == 1


def test_value_converts_numpy_like_scalars(bq):
    obj, published = bq

    class Scalar:  # quacks like numpy.int64
        shape = ()

        def item(self):
            return 7

    obj.value("k", Scalar())
    assert parse_value(as_output(published[0])) == NamedValue(name="k", value=7)


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("bad name", 1, ValueError),
        ("x", True, TypeError),
        ("x", [1, 2], TypeError),
        ("x", math.nan, ValueError),
        ("x", "y" * 201, ValueError),
    ],
)
def test_value_rejects_bad_input(bq, name, value, error):
    obj, published = bq
    with pytest.raises(error):
        obj.value(name, value)
    assert published == []


def test_value_output_matches_the_prelude(bq):
    obj, published = bq
    obj.value("p", 0.0031, unit="p")
    assert value_output("p", 0.0031, unit="p") == as_output(published[0])


def test_parse_value_ignores_other_outputs_and_reports_malformed():
    assert parse_value(KernelOutput(stream=OutputStream.STDOUT, text="hi")) is None
    bad = KernelOutput(stream=OutputStream.DISPLAY, data={VALUE_MIME: '{"name": "1x"}'})
    assert isinstance(parse_value(bad), str)
    assert isinstance(
        parse_value(KernelOutput(stream=OutputStream.DISPLAY, data={VALUE_MIME: "[1]"})), str
    )
