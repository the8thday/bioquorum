import math

import pytest
from pydantic import TypeAdapter, ValidationError

from bioquorum.core import events as ev
from bioquorum.core.refs import ClaimRef, NamedValue, QuoteRef, ValueRef


@pytest.mark.parametrize("value", [412, 0.0312, -1e-12, "IL23R"])
def test_named_value_accepts_scalars(value):
    nv = NamedValue(name="n_de", value=value, unit="genes")
    again = NamedValue.model_validate_json(nv.model_dump_json())
    assert again == nv and type(again.value) is type(value)


@pytest.mark.parametrize("value", [True, [1, 2], {"a": 1}, math.nan, math.inf, "x" * 201, None])
def test_named_value_rejects_non_scalars(value):
    with pytest.raises(ValidationError):
        NamedValue(name="x", value=value)


@pytest.mark.parametrize("name", ["", "1abc", "a b", "a.b", "a-b", "x" * 65, "{{x}}"])
def test_named_value_rejects_bad_names(name):
    with pytest.raises(ValidationError):
        NamedValue(name=name, value=1)


def test_claim_refs_discriminate_bindings():
    adapter = TypeAdapter(list[ClaimRef])
    refs = [ValueRef(name="n", run_id="r1"), QuoteRef(source="s1", call_id="c1", quote="abc")]
    assert adapter.validate_json(adapter.dump_json(refs)) == refs


def test_run_finished_values_default_empty_and_round_trip():
    old = ev.RunFinished.model_validate({"run_id": "r", "run_key": "k"})  # v0.1 payload
    assert old.values == []
    new = ev.RunFinished(run_id="r", run_key="k", values=[NamedValue(name="n", value=3)])
    assert ev.RunFinished.model_validate_json(new.model_dump_json()) == new
