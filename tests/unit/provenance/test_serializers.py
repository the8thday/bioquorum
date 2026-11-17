import pandas as pd

import bioquorum.provenance  # noqa: F401  (installs serializers)
from bioquorum.core.hashing import hash_object


def test_dataframe_hash_is_strong_and_column_order_independent():
    a = pd.DataFrame({"x": [1, 2], "y": [0.5, 1.5]})
    b = a[["y", "x"]]
    ha, hb = hash_object(a), hash_object(b)
    assert ha.serializer == "parquet" and not ha.weak
    assert ha.hash == hb.hash


def test_dataframe_hash_changes_with_values():
    a = pd.DataFrame({"x": [1, 2]})
    assert hash_object(a).hash != hash_object(pd.DataFrame({"x": [1, 3]})).hash


def test_dataframe_hash_is_stable_across_calls():
    df = pd.DataFrame({"g": ["a", "b"], "v": [1.0, 2.0]})
    assert hash_object(df).hash == hash_object(df.copy()).hash
