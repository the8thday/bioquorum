import pandas as pd

from bioquorum.core.enums import OutputStream
from bioquorum.runtime.kernel import KernelOutput
from bioquorum.runtime.summarize import (
    summarize_dataframe,
    summarize_outputs,
    truncate,
)


def test_truncate_keeps_head_and_tail_and_says_so():
    text = "A" * 5000 + "Z" * 5000
    out = truncate(text, 1000)
    assert len(out) <= 1100
    assert out.startswith("A") and out.endswith("Z") and "truncated" in out


def test_short_text_untouched():
    assert truncate("abc", 10) == "abc"


def test_outputs_replace_images_with_labels():
    outs = [
        KernelOutput(stream=OutputStream.STDOUT, text="n=3\n"),
        KernelOutput(
            stream=OutputStream.DISPLAY, data={"image/png": "AAAA", "text/plain": "<Fig>"}
        ),
        KernelOutput(stream=OutputStream.DISPLAY, data={"text/plain": "42"}),
    ]
    text = summarize_outputs(outs, image_labels={1: "[figure 1]"})
    assert text == "n=3\n[figure 1]\n42"
    assert "AAAA" not in text


def test_dataframe_summary_has_shape_dtypes_head_describe():
    df = pd.DataFrame({"gene": list("abcdefg"), "lfc": range(7)})
    s = summarize_dataframe(df, head=2)
    assert "shape=(7, 2)" in s and "dtypes" in s and "head(2)" in s and "describe" in s
    assert "g " not in s.split("describe")[0].split("head(2)")[1]  # only 2 rows shown
